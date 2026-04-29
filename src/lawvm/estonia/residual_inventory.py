"""Deterministic EE residual inventory for known non-zero commensurable pairs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast
from typing import Literal

from lawvm.estonia.residual_evidence import (
    GeneratedEEResidualEvidence,
    build_address_list_family,
    build_inserted_item_omission_family,
    build_inserted_note_omission_family,
    build_inserted_section_omission_family,
    build_shortened_section_family,
)


EEResidualBucket = Literal[
    "replay_bug",
    "source_oracle_drift",
    "source_pathology",
    "source_ambiguity",
    "appendix_display_pathology",
    "oracle_correction_notice",
    "descendant_residual_mix",
]


@dataclass(frozen=True)
class EEResidualRecord:
    """One evidence-backed residual divergence classification."""

    address: str
    bucket: EEResidualBucket
    evidence: str


@dataclass(frozen=True)
class EEPairResidualInventory:
    """Known residual inventory for one EE replay/oracle pair."""

    base_id: str
    oracle_id: str
    statute_title: str
    comparison_class: str
    residuals: tuple[EEResidualRecord, ...] = field(default_factory=tuple)


def _lower_generated_residual_records(
    *families: tuple[GeneratedEEResidualEvidence, ...]
) -> tuple[EEResidualRecord, ...]:
    """Lower generated evidence helpers into inventory-shaped residual records."""
    return tuple(
        EEResidualRecord(address=record.address, bucket=cast(EEResidualBucket, record.bucket), evidence=record.evidence)
        for family in families
        for record in family
    )


_KNOWN_EE_RESIDUALS: dict[tuple[str, str], EEPairResidualInventory] = {
    ("130032012017", "119062013014"): EEPairResidualInventory(
        base_id="130032012017",
        oracle_id="119062013014",
        statute_title=(
            "Alameetme «Väikesemahulise teaduse infrastruktuuri kaasajastamine Eesti "
            "teadus- ja arendusasutuste teadusteemade sihtfinantseerimise raames» tingimused"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:3",
                    "chapter:3/section:13",
                    "chapter:3/section:13/subsection:4",
                ),
                evidence=(
                    "Replay preserves the source/base terminal semicolon in § 13(4). "
                    "Oracle 119062013014 renders the same subsection with a terminal "
                    "period, without a source operation in the pair window that owns "
                    "that punctuation change."
                ),
                bucket="source_oracle_drift",
            ),
            build_address_list_family(
                addresses=(
                    "chapter:4",
                    "chapter:4/section:20",
                    "chapter:4/section:20/subsection:1",
                    "chapter:4/section:20/subsection:1/item:13_1",
                ),
                evidence=(
                    "Source act 119062013001 § 18 item 3 inserts § 20 item 13^1 with "
                    "the literal phrase '10 000 eurot (ilma käibemaksuta. ... "
                    "https://riigihanked.riik.ee ;'. Replay preserves that source "
                    "surface. Oracle 119062013014 instead has the closed parenthesis "
                    "'(ilma käibemaksuta).' and no space before the terminal semicolon. "
                    "This is classified as source/oracle drift pending authority review, "
                    "not as replay-core license to correct amendment text."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("115112016003", "114052020002"): EEPairResidualInventory(
        base_id="115112016003",
        oracle_id="114052020002",
        statute_title="Algatusrühma koostöötegevused",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:8",
                    "section:8/subsection:2",
                    "section:8/subsection:2/item:3",
                ),
                evidence=(
                    "Source act 106092019001 inserts § 8(2) item 4 and changes "
                    "'kuluefektiivsus' wording in item 3, but does not explicitly "
                    "replace item 3's terminal period with a semicolon. Replay "
                    "therefore preserves the source/base terminal punctuation while "
                    "oracle 114052020002 renders item 3 as a non-final list item."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("125012012005", "123092022012"): EEPairResidualInventory(
        base_id="125012012005",
        oracle_id="123092022012",
        statute_title="Alla 24-meetrise pikkusega laeva minimaalse vabaparda määramise nõuded",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:6",
                    "section:6/subsection:9",
                ),
                evidence=(
                    "Source act 129122015004 § 1 item 5 explicitly targets § 6(5) "
                    "and replaces 'peab' with 'peavad'. Replay applies that source "
                    "target. Oracle 123092022012 leaves § 6(5) singular and instead "
                    "renders § 6(9) as 'Kolmnurkade alumised tipud peavad...'. This "
                    "is classified as source/oracle drift; replay must not retarget "
                    "the operation from subsection 5 to subsection 9 by grammatical fit."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("105072012011", "126032014005"): EEPairResidualInventory(
        base_id="105072012011",
        oracle_id="126032014005",
        statute_title="Rahvusvaheliste ürituste ja konverentside toetamise tingimused ja kord",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:1",
                    "chapter:1/section:4",
                    "chapter:1/section:4/subsection:1",
                    "chapter:1/section:4/subsection:1/item:5",
                    "chapter:2",
                    "chapter:2/section:7",
                    "chapter:2/section:7/subsection:2",
                    "chapter:2/section:7/subsection:2/item:3",
                    "chapter:4",
                    "chapter:4/section:15",
                    "chapter:4/section:15/subsection:3",
                    "chapter:4/section:19",
                    "chapter:4/section:19/subsection:5",
                    "chapter:4/section:19/subsection:5/item:8",
                ),
                evidence=(
                    "Source act 126032014003 explicitly inserts § 4 items 6 and 7, "
                    "§ 7(2) items 4-6, and § 19(5) item 9. Replay preserves the "
                    "source-owned new-item terminal punctuation. The remaining item "
                    "5, item 3, and item 8 rows are predecessor terminal punctuation "
                    "differences only; mutating those siblings would require a separate "
                    "owned list-continuation rule, not silent replay repair. The same "
                    "source act also replaces the phrase 'sihtasutuse juhatus' with "
                    "'sihtasutus' throughout the regulation in corresponding case; "
                    "replay therefore produces 'teeb sihtasutus taotleja...', while "
                    "oracle 126032014005 has 'teeb sihtasutuse taotleja...'. This is "
                    "classified as a source/oracle text-surface disagreement pending "
                    "external authority review."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("124112010005", "109062011002"): EEPairResidualInventory(
        base_id="124112010005",
        oracle_id="109062011002",
        statute_title=(
            "«Eesti maaelu arengukava 2007–2013» raames antava tehnilise "
            "abi toetuse saamise nõuded, toetuse taotlemise ja taotluse "
            "menetlemise kord"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:3",
                    "chapter:3/section:8",
                    "chapter:3/section:8/subsection:1",
                ),
                evidence=(
                    "Base 124112010005 carries § 8(1), a historical transitional "
                    "repealer of the prior 2007 regulation. Source act "
                    "109062011001 adds § 8(3) but does not target § 8(1). "
                    "Replay therefore preserves the base/source text, while "
                    "oracle 109062011002 presents § 8(1) as the editorial "
                    "placeholder '[Käesolevast tekstist välja jäetud.]'. This "
                    "is a same-chain consolidated-surface redaction difference, "
                    "not an unexplained replay mutation."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("124072014013", "113012015027"): EEPairResidualInventory(
        base_id="124072014013",
        oracle_id="113012015027",
        statute_title="Innovatsiooniosakute toetusmeetme tingimused ja kord",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:4/subsection:1/item:8",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay preserves the source/base item terminal punctuation after applying "
                    "the 2015-01-09 text replacement. Oracle 113012015027 differs only by "
                    "terminal item punctuation; no amendment in the pair window explicitly "
                    "changes that terminal mark."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:5/subsection:3/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay preserves the source/base item terminal punctuation. Oracle "
                    "113012015027 differs only by terminal item punctuation, without a "
                    "source operation that owns that textual change."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/section:10/subsection:2/item:9",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay preserves the source/base item terminal punctuation. Oracle "
                    "113012015027 differs only by terminal item punctuation, without a "
                    "source operation that owns that textual change."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/section:12/subsection:4/item:9",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay preserves the source/base item terminal punctuation. Oracle "
                    "113012015027 differs only by terminal item punctuation, without a "
                    "source operation that owns that textual change."
                ),
            ),
        ),
    ),
    ("117052024008", "104042025010"): EEPairResidualInventory(
        base_id="117052024008",
        oracle_id="104042025010",
        statute_title="Õli- ja kiudtaimede seemne kategooriad ning õli- ja kiudtaimede seemne tootmise ja turustamise nõuded",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:5_1",
                    "chapter:5_1/section:23_1",
                    "chapter:5_1/section:23_1/subsection:1",
                    "chapter:6",
                    "chapter:6/section:23_1",
                    "chapter:6/section:23_1/subsection:1",
                    "chapter:6/section:24",
                    "chapter:6/section:24/subsection:1",
                    "chapter:6/section:24/subsection:2",
                    "chapter:6/section:24/subsection:3",
                    "chapter:6/section:24/subsection:4",
                    "chapter:7",
                    "chapter:7/section:24",
                    "chapter:7/section:24/subsection:1",
                    "chapter:7/section:24/subsection:2",
                    "chapter:7/section:24/subsection:3",
                    "chapter:7/section:24/subsection:4",
                    "chapter:7/section:25",
                    "chapter:7/section:25/subsection:1",
                    "chapter:7/section:25/subsection:2",
                    "chapter:7/section:26",
                    "chapter:7/section:26/subsection:1",
                    "chapter:8",
                    "chapter:8/section:25",
                    "chapter:8/section:25/subsection:1",
                    "chapter:8/section:25/subsection:2",
                    "chapter:8/section:26",
                    "chapter:8/section:26/subsection:1",
                ),
                evidence=(
                    "The sole source act between 117052024008 and 104042025010 is "
                    "104042025006. Its § 4 block for this target amends only the "
                    "normitehniline märkus and replaces lisa 6; it contains no "
                    "instruction to renumber or move chapters 5^1, 6, 7, or 8. "
                    "The remaining divergences are a whole-tail chapter/section "
                    "address projection difference between replay and oracle, so "
                    "they are classified as source/oracle structural drift rather "
                    "than replay-core mutation debt."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("129052012009", "128032013017"): EEPairResidualInventory(
        base_id="129052012009",
        oracle_id="128032013017",
        statute_title="Üldosakonna põhimäärus",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:16",
                    "section:16/subsection:1",
                    "section:16/subsection:1/item:16",
                ),
                evidence=(
                    "Source act 128032013013 amends § 16 items 2, 3^1, 4, 5, 6, "
                    "and 9, but does not target item 16. Replay therefore preserves "
                    "the source/base terminal period in § 16 item 16, while oracle "
                    "128032013017 changes only that terminal punctuation to a "
                    "semicolon before the following unchanged items. This is bounded "
                    "oracle-surface punctuation drift, not replay omission."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("115122011012", "128032013018"): EEPairResidualInventory(
        base_id="115122011012",
        oracle_id="128032013018",
        statute_title="Õiguspoliitika osakonna põhimäärus",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:2",
                    "section:2/subsection:1",
                    "section:2/subsection:1/item:1",
                    "section:8",
                    "section:8/subsection:1",
                    "section:8/subsection:1/item:7_1",
                    "section:8/subsection:1/item:7_2",
                    "section:8/subsection:1/item:7_3",
                    "section:11",
                    "section:11/subsection:1",
                    "section:11/subsection:1/item:8",
                ),
                evidence=(
                    "Source act 128032013013 § 3 targets the Õiguspoliitika "
                    "osakonna põhimäärus with a global 'amet' -> 'ametikoht' "
                    "case-inflected replacement, three targeted 'teenistuja' "
                    "replacement groups, and repeal of § 6. It does not target "
                    "§ 2 item 1 terminal punctuation, the pre-existing malformed "
                    "§ 8 item 7^1/7^2/7^3 run, or § 11 item 8 terminal "
                    "punctuation. Replay therefore preserves the base/source "
                    "structure and punctuation, while oracle 128032013018 presents "
                    "a cleaned consolidated surface. This is source/oracle surface "
                    "drift, not replay-core mutation debt."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("115032011010", "101092011004"): EEPairResidualInventory(
        base_id="115032011010",
        oracle_id="101092011004",
        statute_title=(
            "2011. aastal toetatavad „Euroopa Kalandusfondi 2007–2013 "
            "rakenduskava” meetmed ja tegevuste liigid"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:1",
                    "section:1/subsection:1",
                    "section:1/subsection:1/item:11",
                ),
                evidence=(
                    "Source act 101092011002 inserts § 1(1) item 12. Replay "
                    "inserts the source-owned new item and preserves the old final "
                    "item 11 terminal period; existing Estonia apply tests require "
                    "that sibling terminal punctuation is not silently mutated on "
                    "append-only item insertion. Oracle 101092011004 changes item "
                    "11 to a semicolon to display the extended list. This is bounded "
                    "source/oracle list-punctuation drift, not a missing replay "
                    "operation."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("115092020007", "130092020007"): EEPairResidualInventory(
        base_id="115092020007",
        oracle_id="130092020007",
        statute_title=(
            "2020. aastal toetatavate „Euroopa Merendus- ja Kalandusfondi "
            "rakenduskava 2014–2020” meetmete ja tegevuste liigid"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:1",
                    "section:1/subsection:1",
                    "section:1/subsection:1/item:21",
                ),
                evidence=(
                    "Source act 130092020005 repeals § 1(1) items 2, 4, 15, "
                    "and 16 and inserts item 22. Replay applies those source-owned "
                    "mutations and preserves the surviving item 21 terminal period; "
                    "existing Estonia apply tests require that append-only item "
                    "insertion does not silently rewrite the previous sibling's "
                    "terminal punctuation. Oracle 130092020007 changes item 21 to "
                    "a semicolon to display the extended list. This is bounded "
                    "source/oracle list-punctuation drift, not a missing replay "
                    "operation."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("113052023002", "109062023008"): EEPairResidualInventory(
        base_id="113052023002",
        oracle_id="109062023008",
        statute_title=(
            "2023. aastal „Eesti maaelu arengukava 2014–2020” alusel "
            "antavad Euroopa Liidu ühise põllumajanduspoliitika kohased "
            "maaelu arengu toetused"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:2",
                    "section:2/subsection:3",
                    "section:2/subsection:3/item:2",
                ),
                evidence=(
                    "Source act 109062023005 § 2 inserts § 2(3) item 3. Replay "
                    "inserts the source-owned new item and preserves the surviving "
                    "item 2 terminal period; existing Estonia apply tests require "
                    "that append-only item insertion does not silently rewrite the "
                    "previous sibling's terminal punctuation. Oracle 109062023008 "
                    "changes item 2 to a semicolon to display the extended list. "
                    "This is bounded source/oracle list-punctuation drift, not a "
                    "missing replay operation."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("125092019004", "122062021002"): EEPairResidualInventory(
        base_id="125092019004",
        oracle_id="122062021002",
        statute_title="Advokaadi kinnitamistoimingud",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:8",
                    "section:8/subsection:4",
                    "section:8/subsection:4/item:4",
                    "section:8/subsection:4/item:5",
                    "section:8/subsection:4/item:7",
                    "section:8/subsection:4/item:8",
                ),
                evidence=(
                    "Source act 125092019001 repeals § 8(5) but does not target "
                    "§ 8(4) items 4, 5, 7, or 8; source act 122062021001 later "
                    "repeals § 10 only. Replay therefore preserves the base/source "
                    "terminal punctuation and capitalization in the untouched § 8(4) "
                    "item list, while oracle 122062021002 presents a cleaned "
                    "semicolon/lowercase list surface. This is bounded source/oracle "
                    "list-punctuation drift, not replay mutation debt."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("123112010049", "122072011005"): EEPairResidualInventory(
        base_id="123112010049",
        oracle_id="122072011005",
        statute_title=(
            "Leader-meetme raames antava kohaliku tegevusgrupi toetuse ja "
            "projektitoetuse saamise nõuded, toetuse taotlemise ja taotluse "
            "menetlemise täpsem kord"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:2/division:1/section:10/subsection:8",
                    "chapter:3/division:3/section:37/subsection:3",
                ),
                evidence=(
                    "Source act 122072011003 inserts text after a quoted surface "
                    "that includes a terminal period. Replay preserves the "
                    "source-literal period before the inserted conjunction, while "
                    "oracle 122072011005 presents the sentence with that period "
                    "removed. This is bounded source/oracle punctuation drift."
                ),
                bucket="source_oracle_drift",
            ),
            build_address_list_family(
                addresses=(
                    "chapter:2/division:2/section:12/subsection:1/item:2",
                ),
                evidence=(
                    "Source act 122072011003 repeals only § 12 item 3. Replay "
                    "finalizes the last surviving item 2 with a period; oracle "
                    "122072011005 keeps item 2's semicolon because the repealed "
                    "following item is still represented editorially. This is "
                    "bounded repealed-item display punctuation drift."
                ),
                bucket="source_oracle_drift",
            ),
        ),
    ),
    ("130042020010", "109032023008"): EEPairResidualInventory(
        base_id="130042020010",
        oracle_id="109032023008",
        statute_title="Majutuse ja toitlustuse erialade riiklik õppekava",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:4_1",
                    "chapter:4_1/section:15_1",
                    "chapter:4_1/section:15_1/subsection:1",
                    "chapter:4_1/section:15_1/subsection:2",
                    "chapter:4_1/section:15_1/subsection:2/item:1",
                    "chapter:4_1/section:15_1/subsection:2/item:10",
                    "chapter:4_1/section:15_1/subsection:2/item:11",
                    "chapter:4_1/section:15_1/subsection:2/item:2",
                    "chapter:4_1/section:15_1/subsection:2/item:3",
                    "chapter:4_1/section:15_1/subsection:2/item:4",
                    "chapter:4_1/section:15_1/subsection:2/item:5",
                    "chapter:4_1/section:15_1/subsection:2/item:6",
                    "chapter:4_1/section:15_1/subsection:2/item:7",
                    "chapter:4_1/section:15_1/subsection:2/item:8",
                    "chapter:4_1/section:15_1/subsection:2/item:9",
                    "chapter:4_1/section:15_2",
                    "chapter:4_1/section:15_2/subsection:1",
                    "chapter:4_1/section:15_2/subsection:2",
                    "chapter:4_1/section:15_3",
                    "chapter:4_1/section:15_3/subsection:1",
                    "chapter:4_1/section:15_3/subsection:1/item:1",
                    "chapter:4_1/section:15_3/subsection:1/item:2",
                    "chapter:4_1/section:15_3/subsection:1/item:3",
                    "chapter:4_1/section:15_3/subsection:2",
                    "chapter:4_1/section:15_3/subsection:2/item:1",
                    "chapter:4_1/section:15_3/subsection:2/item:2",
                    "chapter:4_1/section:15_3/subsection:2/item:3",
                    "chapter:4_1/section:15_3/subsection:2/item:4",
                    "chapter:4_1/section:15_3/subsection:2/item:5",
                    "chapter:4_1/section:15_3/subsection:2/item:6",
                    "chapter:4_1/section:15_3/subsection:2/item:7",
                    "chapter:4_1/section:15_3/subsection:2/item:8",
                    "chapter:4_1/section:15_3/subsection:2/item:9",
                    "chapter:4_1/section:15_4",
                    "chapter:4_1/section:15_4/subsection:1",
                ),
                evidence=(
                    "Source act 109032023004 says 'määruse lisa 5 tunnistatakse "
                    "kehtetuks'. LawVM currently materializes the appendix body as "
                    "chapter 4^1, while oracle 109032023008 keeps the chapter and "
                    "section headings as repealed placeholders with descendants "
                    "removed. This is a known appendix projection/repeal display "
                    "pathology, not an RT candidate divergence."
                ),
                bucket="appendix_display_pathology",
            ),
        ),
    ),
    ("115052020021", "128082021009"): EEPairResidualInventory(
        base_id="115052020021",
        oracle_id="128082021009",
        statute_title="Nakkushaiguste tõrje nõuded",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "section:2",
                    "section:2/subsection:1",
                    "section:2/subsection:1/item:1",
                    "section:2/subsection:1/item:10",
                    "section:2/subsection:1/item:11",
                    "section:2/subsection:1/item:12",
                    "section:2/subsection:1/item:13",
                    "section:2/subsection:1/item:14",
                    "section:2/subsection:1/item:15",
                    "section:2/subsection:1/item:16",
                    "section:2/subsection:1/item:17",
                    "section:2/subsection:1/item:18",
                    "section:2/subsection:1/item:19",
                    "section:2/subsection:1/item:2",
                    "section:2/subsection:1/item:20",
                    "section:2/subsection:1/item:21",
                    "section:2/subsection:1/item:22",
                    "section:2/subsection:1/item:23",
                    "section:2/subsection:1/item:24",
                    "section:2/subsection:1/item:25",
                    "section:2/subsection:1/item:26",
                    "section:2/subsection:1/item:27",
                    "section:2/subsection:1/item:28",
                    "section:2/subsection:1/item:29",
                    "section:2/subsection:1/item:3",
                    "section:2/subsection:1/item:4",
                    "section:2/subsection:1/item:5",
                    "section:2/subsection:1/item:6",
                    "section:2/subsection:1/item:7",
                    "section:2/subsection:1/item:8",
                    "section:2/subsection:1/item:9",
                ),
                evidence=(
                    "Source act 128082021006 amends the appendix, specifically "
                    "'määruse lisa punkti 3^1 alapunkt 3^1.2' and '3^1.3'. "
                    "The current EE frontend does not yet model appendix numbered "
                    "disease-control subpoints as independently addressable legal "
                    "state, so replay leaves the previous appendix text while "
                    "oracle 128082021009 reflects the appendix updates. This is an "
                    "appendix projection gap, not an RT candidate divergence."
                ),
                bucket="appendix_display_pathology",
            ),
        ),
    ),
    ("104072013022", "104072013023"): EEPairResidualInventory(
        base_id="104072013022",
        oracle_id="104072013023",
        statute_title=(
            "Täiendavad juhised tollideklaratsiooni ja lihtsustatud tollideklaratsiooni "
            "täitmiseks, esitamiseks ja aktsepteerimiseks"
        ),
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:2",
                    "chapter:2/section:3/subsection:10",
                    "chapter:2/section:3/subsection:11",
                    "chapter:2/section:3/subsection:12",
                    "chapter:2/section:3/subsection:13",
                    "chapter:2/section:3/subsection:13/item:1",
                    "chapter:2/section:3/subsection:14",
                    "chapter:2/section:3/subsection:14/item:1",
                    "chapter:2/section:3/subsection:14/item:2",
                    "chapter:2/section:3/subsection:15",
                    "chapter:2/section:3/subsection:15/item:2",
                    "chapter:2/section:3/subsection:15/item:3",
                    "chapter:2/section:3/subsection:16",
                    "chapter:2/section:3/subsection:16/item:3",
                    "chapter:2/section:3/subsection:17",
                    "chapter:2/section:3/subsection:18",
                    "chapter:2/section:3/subsection:19",
                    "chapter:2/section:3/subsection:20",
                    "chapter:2/section:3/subsection:21",
                    "chapter:2/section:3/subsection:22",
                    "chapter:2/section:3/subsection:23",
                    "chapter:2/section:3/subsection:24",
                    "chapter:2/section:3/subsection:25",
                    "chapter:2/section:3/subsection:26",
                    "chapter:2/section:3/subsection:27",
                    "chapter:2/section:3/subsection:28",
                    "chapter:2/section:3/subsection:29",
                    "chapter:2/section:3/subsection:30",
                    "chapter:2/section:3/subsection:31",
                    "chapter:2/section:3/subsection:32",
                    "chapter:2/section:3/subsection:33",
                    "chapter:2/section:3/subsection:34",
                    "chapter:2/section:3/subsection:35",
                    "chapter:2/section:3/subsection:36",
                    "chapter:2/section:3/subsection:37",
                    "chapter:2/section:3/subsection:38",
                    "chapter:2/section:3/subsection:39",
                    "chapter:2/section:3/subsection:40",
                    "chapter:2/section:3/subsection:41",
                    "chapter:2/section:3/subsection:42",
                    "chapter:2/section:3/subsection:43",
                    "chapter:2/section:3/subsection:44",
                    "chapter:2/section:3/subsection:45",
                    "chapter:2/section:3/subsection:46",
                    "chapter:2/section:3/subsection:47",
                    "chapter:2/section:3/subsection:48",
                    "chapter:2/section:3/subsection:49",
                    "chapter:2/section:3/subsection:50",
                    "chapter:2/section:3/subsection:51",
                    "chapter:2/section:3/subsection:52",
                    "chapter:2/section:3/subsection:53",
                    "chapter:2/section:3/subsection:54",
                    "chapter:2/section:3/subsection:55",
                    "chapter:2/section:3/subsection:9",
                    "chapter:2/section:4",
                    "chapter:2/section:4/subsection:20",
                    "chapter:2/section:4/subsection:21",
                    "chapter:2/section:4/subsection:22",
                    "chapter:2/section:4/subsection:23",
                    "chapter:2/section:4/subsection:24",
                    "chapter:2/section:4/subsection:25",
                    "chapter:2/section:4/subsection:26",
                    "chapter:2/section:4/subsection:27",
                    "chapter:2/section:4/subsection:28",
                    "chapter:2/section:4/subsection:29",
                    "chapter:2/section:4/subsection:30",
                    "chapter:2/section:4/subsection:31",
                    "chapter:2/section:4/subsection:32",
                    "chapter:2/section:4/subsection:33",
                    "chapter:2/section:4/subsection:34",
                    "chapter:2/section:4/subsection:35",
                    "chapter:2/section:4/subsection:36",
                    "chapter:2/section:4/subsection:37",
                    "chapter:2/section:4/subsection:38",
                    "chapter:2/section:4/subsection:39",
                    "chapter:2/section:4/subsection:40",
                    "chapter:2/section:4/subsection:41",
                    "chapter:2/section:4/subsection:42",
                    "chapter:2/section:4/subsection:43",
                    "chapter:2/section:4/subsection:44",
                    "chapter:2/section:4/subsection:45",
                    "chapter:2/section:4/subsection:46",
                    "chapter:2/section:4/subsection:47",
                    "chapter:2/section:4/subsection:48",
                    "chapter:2/section:4/subsection:49",
                    "chapter:2/section:4/subsection:50",
                    "chapter:2/section:4/subsection:51",
                    "chapter:2/section:4/subsection:52",
                    "chapter:2/section:4/subsection:53",
                    "chapter:2/section:4/subsection:54",
                    "chapter:2/section:4/subsection:55",
                    "chapter:2/section:4/subsection:56",
                    "chapter:2/section:4/subsection:57",
                    "chapter:2/section:4/subsection:58",
                    "chapter:2/section:4/subsection:59",
                    "chapter:2/section:4/subsection:60",
                    "chapter:2/section:4/subsection:61",
                    "chapter:2/section:4/subsection:62",
                    "chapter:2/section:4/subsection:63",
                    "chapter:2/section:4/subsection:64",
                    "chapter:2/section:4/subsection:65",
                    "chapter:2/section:4/subsection:66",
                    "chapter:2/section:4/subsection:67",
                    "chapter:2/section:4/subsection:68",
                    "chapter:2/section:4/subsection:69",
                    "chapter:2/section:4/subsection:70",
                    "chapter:2/section:4/subsection:71",
                    "chapter:2/section:4/subsection:72",
                    "chapter:2/section:4/subsection:73",
                    "chapter:2/section:4/subsection:74",
                ),
                evidence=(
                    "Source act 104072013020 delays only § 1 items 1 and 3 to "
                    "2014-01-01; LawVM now applies only those two field-text "
                    "updates. The remaining same-chain residuals are parent and "
                    "descendant echoes of RT consolidated surface 104072013023 "
                    "inserting empty placeholder children before the Lahter 13 "
                    "and Lahter 23 runs in §§ 3-4, shifting later child addresses "
                    "without a source-backed text-state mutation."
                ),
                bucket="source_oracle_drift",
            )
        ),
    ),
    ("128052013004", "109052017038"): EEPairResidualInventory(
        base_id="128052013004",
        oracle_id="109052017038",
        statute_title="Proovivõtumeetodid",
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:3",
                bucket="source_ambiguity",
                evidence=(
                    "Parent echo of § 10(8). Source act 109052017037 says "
                    "§ 10 subsections 6, 7 and 8 are rewritten, but base "
                    "surface 128052013004 exposes § 10 only through subsection "
                    "7. Replay applies the live § 10(6) and § 10(7) replacements "
                    "and leaves the explicit absent § 10(8) target unresolved "
                    "rather than silently converting replace into insert."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/section:10",
                bucket="source_ambiguity",
                evidence=(
                    "Parent echo of § 10(8). Source act 109052017037 says "
                    "§ 10 subsections 6, 7 and 8 are rewritten, but base "
                    "surface 128052013004 exposes § 10 only through subsection "
                    "7. Replay applies the live § 10(6) and § 10(7) replacements "
                    "and leaves the explicit absent § 10(8) target unresolved "
                    "rather than silently converting replace into insert."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/section:10/subsection:8",
                bucket="source_ambiguity",
                evidence=(
                    "Source act 109052017037 includes a payload for § 10(8) in "
                    "the clause 'paragrahvi 10 lõiked 6, 7 ja 8 sõnastatakse', "
                    "while base surface 128052013004 has no live § 10(8). "
                    "Oracle 109052017038 contains the payload as § 10(8); "
                    "LawVM preserves this as an absent explicit replace target."
                ),
            ),
        )),
    ),
    ("129032014010", "130042015006"): EEPairResidualInventory(
        base_id="129032014010",
        oracle_id="130042015006",
        statute_title=(
            "Poolloodusliku koosluse hooldamise toetuse saamise nõuded, toetuse "
            "taotlemise ja taotluse menetlemise täpsem kord aastateks 2007–2013"
        ),
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Parent echo of § 6(2) item 4 terminal punctuation. Source act "
                    "130042015004 repeals § 6(2) item 5 and does not rewrite item 4; "
                    "the remaining difference is the semicolon/full-stop surface "
                    "before the repealed placeholder item."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Parent echo of § 6(2) item 4 terminal punctuation. Source act "
                    "130042015004 repeals § 6(2) item 5 and does not rewrite item 4; "
                    "the remaining difference is the semicolon/full-stop surface "
                    "before the repealed placeholder item."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Parent echo of § 6(2) item 4 terminal punctuation. Source act "
                    "130042015004 repeals § 6(2) item 5 and does not rewrite item 4; "
                    "the remaining difference is the semicolon/full-stop surface "
                    "before the repealed placeholder item."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6/subsection:2/item:4",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130042015004 repeals § 6(2) item 5 and does not "
                    "rewrite item 4. Replay and oracle differ only on whether item 4 "
                    "keeps the semicolon before the repealed placeholder item or is "
                    "finalized with a full stop."
                ),
            ),
            EEResidualRecord(
                address="chapter:7",
                bucket="source_oracle_drift",
                evidence=(
                    "Parent echo of § 17(9)'s display of the superscript subsection "
                    "reference § 14(2^1). Source act 130042015004 carries the "
                    "superscript form; replay comparison text renders it as '2 1', "
                    "while oracle 130042015006 renders the same reference as '21'."
                ),
            ),
            EEResidualRecord(
                address="chapter:7/section:17",
                bucket="source_oracle_drift",
                evidence=(
                    "Parent echo of § 17(9)'s display of the superscript subsection "
                    "reference § 14(2^1). Source act 130042015004 carries the "
                    "superscript form; replay comparison text renders it as '2 1', "
                    "while oracle 130042015006 renders the same reference as '21'."
                ),
            ),
            EEResidualRecord(
                address="chapter:7/section:17/subsection:9",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130042015004 inserts § 17(9) with a superscript "
                    "reference to § 14(2^1). Replay comparison text renders that "
                    "reference as '2 1', while oracle 130042015006 renders it as "
                    "'21'. This is a bounded superscript display surface difference."
                ),
            ),
        )),
    ),
    ("103052013007", "130042015007"): EEPairResidualInventory(
        base_id="103052013007",
        oracle_id="130042015007",
        statute_title=(
            "Keskkonnasõbraliku majandamise toetuse saamise nõuded, toetuse "
            "taotlemise ja taotluse menetlemise täpsem kord"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:2",
                    "chapter:2/section:5",
                    "chapter:2/section:5/subsection:1",
                    "chapter:2/section:5/subsection:1/item:5",
                    "chapter:2/section:5/subsection:1/item:8",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "After filtering omnibus source act 129032014003 to the "
                    "matching § 1 target only, the remaining § 5(1) residuals "
                    "are source/oracle drift. Source act 129032014003 § 1 item "
                    "3 repeals § 5(1) item 5, and source act 130042015004 § 1 "
                    "does not reinsert or rewrite that item. Source act "
                    "130042015004 § 1 item 8 rewrites § 5(1) item 8 with only "
                    "the first manure/nitrogen sentence, while oracle "
                    "130042015007 keeps the repealed training item and retains "
                    "additional old nitraaditundlik-area sentences under item 8."
                ),
            ),
        ),
    ),
    ("106012015009", "113092017002"): EEPairResidualInventory(
        base_id="106012015009",
        oracle_id="113092017002",
        statute_title=(
            "Jahitunnistuse vorm, jahiteooriaeksami ja laskekatse sooritamise "
            "ning jahitunnistuse taotlemise ja andmise kord, jahindusalasele "
            "koolitusele ja koolitajale esitatavad nõuded ning koolitamise kord"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:2/section:10",
                    "chapter:2/section:10/subsection:1",
                    "chapter:2/section:10/subsection:2",
                    "chapter:2/section:10/subsection:3",
                    "chapter:2/section:10/subsection:4",
                    "chapter:2/section:10/subsection:5",
                    "chapter:2/section:10/subsection:6",
                    "chapter:2/section:10/subsection:7",
                    "chapter:2/section:10/subsection:8",
                    "chapter:2/section:10/subsection:9",
                    "chapter:2/section:11",
                    "chapter:2/section:11/subsection:1",
                    "chapter:2/section:11/subsection:2",
                    "chapter:2/section:11/subsection:3",
                    "chapter:2/section:11/subsection:4",
                    "chapter:2/section:11/subsection:5",
                    "chapter:2/section:11/subsection:6",
                    "chapter:2/section:11/subsection:7",
                    "chapter:2/section:11/subsection:8",
                    "chapter:2/section:11/subsection:9",
                    "chapter:2/section:12",
                    "chapter:2/section:12/subsection:1",
                    "chapter:2/section:12/subsection:2",
                    "chapter:2/section:12/subsection:3",
                    "chapter:2/section:12/subsection:4",
                    "chapter:2/section:12/subsection:5",
                    "chapter:2/section:13",
                    "chapter:2/section:13/subsection:1",
                    "chapter:2/section:13/subsection:1/item:1",
                    "chapter:2/section:13/subsection:1/item:2",
                    "chapter:2/section:13/subsection:1/item:3",
                    "chapter:2/section:13/subsection:1/item:4",
                    "chapter:2/section:13/subsection:2",
                    "chapter:2/section:13/subsection:3",
                    "chapter:2/section:14",
                    "chapter:2/section:14/subsection:1",
                    "chapter:2/section:14/subsection:1/item:1",
                    "chapter:2/section:14/subsection:1/item:2",
                    "chapter:2/section:14/subsection:1/item:3",
                    "chapter:2/section:14/subsection:1/item:4",
                    "chapter:2/section:14/subsection:2",
                    "chapter:2/section:14/subsection:3",
                    "chapter:2/section:14/subsection:4",
                    "chapter:2/section:14/subsection:5",
                    "chapter:2/section:14/subsection:6",
                    "chapter:2/section:15",
                    "chapter:2/section:15/subsection:1",
                    "chapter:2/section:15/subsection:1/item:2",
                    "chapter:2/section:15/subsection:1/item:3",
                    "chapter:2/section:15/subsection:3",
                    "chapter:2/section:15/subsection:4",
                    "chapter:2/section:15/subsection:5",
                    "chapter:2/section:15/subsection:6",
                    "chapter:2/section:16",
                    "chapter:2/section:16/subsection:1",
                    "chapter:2/section:16/subsection:1/item:1",
                    "chapter:2/section:16/subsection:1/item:2",
                    "chapter:2/section:16/subsection:1/item:3",
                    "chapter:2/section:16/subsection:2",
                    "chapter:2/section:16/subsection:3",
                    "chapter:2/section:16/subsection:4",
                    "chapter:2/section:17",
                    "chapter:2/section:17/subsection:1",
                    "chapter:2/section:17/subsection:2",
                    "chapter:2/section:17/subsection:3",
                    "chapter:2/section:17/subsection:4",
                    "chapter:2/section:18",
                    "chapter:2/section:18/subsection:1",
                    "chapter:2/section:18/subsection:2",
                    "chapter:2/section:18/subsection:3",
                    "chapter:2/section:18/subsection:4",
                    "chapter:2/section:6",
                    "chapter:2/section:6/subsection:1",
                    "chapter:2/section:7",
                    "chapter:2/section:7/subsection:1",
                    "chapter:2/section:7/subsection:2",
                    "chapter:2/section:7/subsection:3",
                    "chapter:2/section:8",
                    "chapter:2/section:8/subsection:1",
                    "chapter:2/section:8/subsection:2",
                    "chapter:2/section:8/subsection:3",
                    "chapter:2/section:8/subsection:4",
                    "chapter:2/section:8/subsection:5",
                    "chapter:2/section:8/subsection:6",
                    "chapter:2/section:8/subsection:7",
                    "chapter:2/section:8/subsection:8",
                    "chapter:2/section:9",
                    "chapter:2/section:9/subsection:1",
                    "chapter:2/section:9/subsection:2",
                    "chapter:2/section:9/subsection:3",
                    "chapter:2/section:9/subsection:4",
                    "chapter:2/section:9/subsection:5",
                    "chapter:2/section:9/subsection:6",
                    "chapter:2/section:9/subsection:7",
                    "chapter:2/section:9/subsection:8",
                    "chapter:3/section:18",
                    "chapter:3/section:18/subsection:1",
                    "chapter:3/section:18/subsection:1/item:1",
                    "chapter:3/section:18/subsection:1/item:2",
                    "chapter:3/section:18/subsection:1/item:3",
                    "chapter:3/section:18/subsection:1/item:4",
                    "chapter:3/section:18/subsection:2",
                    "chapter:3/section:18/subsection:3",
                    "chapter:3/section:18/subsection:3/item:1",
                    "chapter:3/section:18/subsection:3/item:2",
                    "chapter:3/section:19",
                    "chapter:3/section:19/subsection:1",
                    "chapter:3/section:19/subsection:1/item:2",
                    "chapter:3/section:19/subsection:1/item:3",
                    "chapter:3/section:19/subsection:1/item:4",
                    "chapter:3/section:19/subsection:2",
                    "chapter:3/section:19/subsection:2/item:1",
                    "chapter:3/section:19/subsection:2/item:2",
                    "chapter:3/section:19/subsection:3",
                    "chapter:3/section:19/subsection:3/item:1",
                    "chapter:3/section:19/subsection:3/item:2",
                    "chapter:3/section:20",
                    "chapter:3/section:20/subsection:1",
                    "chapter:3/section:20/subsection:1/item:2",
                    "chapter:3/section:20/subsection:1/item:3",
                    "chapter:3/section:21",
                    "chapter:3/section:21/subsection:1",
                    "chapter:3/section:21/subsection:1/item:1",
                    "chapter:3/section:21/subsection:1/item:2",
                    "chapter:3/section:21/subsection:1/item:3",
                    "chapter:3/section:21/subsection:2",
                    "chapter:3/section:21/subsection:2/item:1",
                    "chapter:3/section:21/subsection:2/item:2",
                    "chapter:3/section:22",
                    "chapter:3/section:22/subsection:1",
                    "chapter:3/section:22/subsection:2",
                    "chapter:3/section:23",
                    "chapter:3/section:23/subsection:1",
                    "chapter:3/section:23/subsection:1/item:1",
                    "chapter:3/section:23/subsection:1/item:2",
                    "chapter:3/section:23/subsection:2",
                    "chapter:3/section:23/subsection:2/item:1",
                    "chapter:3/section:23/subsection:2/item:2",
                    "chapter:3/section:23/subsection:3",
                    "chapter:3/section:24",
                    "chapter:3/section:24/subsection:1",
                    "chapter:3/section:24/subsection:1/item:1",
                    "chapter:3/section:24/subsection:1/item:2",
                    "chapter:3/section:24/subsection:2",
                    "chapter:3/section:24/subsection:2/item:1",
                    "chapter:3/section:24/subsection:2/item:2",
                    "chapter:3/section:24/subsection:3",
                    "chapter:4/section:24",
                    "chapter:4/section:24/subsection:1",
                    "chapter:4/section:24/subsection:2",
                    "chapter:4/section:24/subsection:3",
                    "chapter:4/section:24/subsection:3/item:1",
                    "chapter:4/section:24/subsection:3/item:2",
                    "chapter:4/section:24/subsection:4",
                    "chapter:4/section:25",
                    "chapter:4/section:25/subsection:1",
                    "chapter:4/section:25/subsection:2",
                    "chapter:4/section:25/subsection:3",
                    "chapter:4/section:25/subsection:3/item:1",
                    "chapter:4/section:25/subsection:3/item:2",
                    "chapter:4/section:25/subsection:4",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Base 106012015009 preserves the original RT section labels §§ 1-5 "
                    "and §§ 7-25, with no § 6 in the parsed body. Source act "
                    "113092017001 only replaces lisa 5 and contains no section renumber "
                    "or body rewrite instruction. Oracle 113092017002 silently closes "
                    "the old numbering gap by relabeling body §§ 7-25 to §§ 6-24; "
                    "replay therefore preserves the source-backed base labels and "
                    "classifies the title-preserving label-offset cascade as "
                    "source/oracle surface drift."
                ),
            ),
        ),
    ),
    ("104082015013", "119082015009"): EEPairResidualInventory(
        base_id="104082015013",
        oracle_id="119082015009",
        statute_title=(
            "Meetme „Linnaliste piirkondade arendamine“ tingimused ja "
            "investeeringute kava koostamise kord"
        ),
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:3",
                    "chapter:3/section:12",
                    "chapter:3/section:12/subsection:9",
                    "chapter:3/section:13_2",
                    "chapter:3/section:13_2/subsection:1",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 119082015001 § 8 lists exact replacement targets: "
                    "§ 12 lõigetes 1, 2^1, 3^1, 4 ja 6 and § 13^2 lõigetes "
                    "1, 3, 6, 8, 9 ja 11. LawVM applies that explicit target "
                    "list. Oracle 119082015009 also changes unlisted § 12(9) "
                    "and normalizes the pre-existing § 13^2(1) source wording "
                    "'Siseministeeriumi algatada' to nominative "
                    "'Rahandusministeerium algatada'. Those residual edits are "
                    "not source-backed by the exact § 8 target list."
                ),
            ),
        ),
    ),
    ("118062021013", "130062023099"): EEPairResidualInventory(
        base_id="118062021013",
        oracle_id="130062023099",
        statute_title="Vedelkütusevaru seadus",
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:6",
                bucket="source_oracle_drift",
                evidence=(
                    "The chapter-level mismatch is inherited from § 22. Source act "
                    "130062023001 contains targeted 'Majandus- ja "
                    "Kommunikatsiooniministeerium' -> 'Kliimaministeerium' rewrites "
                    "for §§ 3_1, 7, 7_2, 11, 11_1, and 18 of Vedelkütusevaru seadus, "
                    "but no visible in-range clause targets § 22. Oracle "
                    "130062023099 nevertheless rewrites § 22 to Kliimaministeerium."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:22",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130062023001 rewrites the ministry name in several "
                    "enumerated Vedelkütusevaru seadus provisions but does not include "
                    "§ 22. Replay therefore preserves 'Majandus- ja "
                    "Kommunikatsiooniministeerium' in § 22, while oracle 130062023099 "
                    "has 'Kliimaministeerium'."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:22/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "The live § 22(1) text names 'Majandus- ja "
                    "Kommunikatsiooniministeerium' as the extra-judicial misdemeanour "
                    "processor. Source act 130062023001 has no visible § 22 target for "
                    "the ministry rewrite; oracle 130062023099 changes the same "
                    "sentence to 'Kliimaministeerium'."
                ),
            ),
        )),
    ),
    ("117032011030", "120122011010"): EEPairResidualInventory(
        base_id="117032011030",
        oracle_id="120122011010",
        statute_title=(
            "Investeerimisühingu ja investeerimisühingu konsolideerimisgrupi "
            "usaldatavusnormatiivide rakendamise, arvutamise ja aruandluse kord "
            "ning riskijuhtimise, omavahendite ja kapitali adekvaatsuse kohta "
            "teabe avalikustamise kord"
        ),
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:3/division:2/section:141/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry mathematical comparison symbols in the "
                    "regulatory coefficient table. Oracle 120122011010 renders at "
                    "least one of those symbols as '?'. This is a bounded RT "
                    "symbol-display surface drift, not a missing amendment."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:2/section:184/subsection:2/item:5",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 120122011001 rewrites § 184(2) item 5 with a "
                    "semicolon-terminated list item. Replay applies the source "
                    "payload. Oracle 120122011010 differs only in the item terminal "
                    "punctuation."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:2/section:220/subsection:1/item:9",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry the Greek beta symbol in the formula item "
                    "('β=1,4.'). Oracle 120122011010 renders the same symbol as "
                    "'?'. This is a bounded RT symbol-display surface drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:2/section:231/subsection:4/item:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry the Greek alpha symbol in § 231(4) item 1. "
                    "Oracle 120122011010 renders the symbol as '?'. This is a "
                    "bounded RT symbol-display surface drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:3/section:254/subsection:1/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry the Greek sigma symbol in the veega-risk "
                    "formula item. Oracle 120122011010 renders the symbol as '?'. "
                    "This is a bounded RT symbol-display surface drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:284/subsection:3/item:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 120122011001, § 1 item 39, replaces only § 284(3) "
                    "item 1. Replay matches that source-backed item-1 payload. "
                    "Oracle 120122011010 retains older item-1 wording while folding "
                    "the replacement material into the following item."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:284/subsection:3/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 120122011001, § 1 item 39, targets § 284(3) item 1 "
                    "only. Replay preserves item 2 as the next live item. Oracle "
                    "120122011010 carries the source-backed item-1 replacement under "
                    "item 2, creating an item-alignment drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:288/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry comparison symbols in the § 288(2) table. "
                    "Oracle 120122011010 renders at least one table comparison "
                    "symbol as '?'. This is a bounded RT symbol-display surface "
                    "drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:289/subsection:4",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay/base carry comparison symbols in the § 289(4) table. "
                    "Oracle 120122011010 renders at least one table comparison "
                    "symbol as '?'. This is a bounded RT symbol-display surface "
                    "drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:310/subsection:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 120122011001, § 1 item 56, replaces full § 310. "
                    "Replay applies the source-backed new § 310(6). Oracle "
                    "120122011010 keeps the pre-amendment § 310(6) wording."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:310_5/subsection:1/item:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 120122011001, § 1 item 57, inserts §§ 310^1-310^8. "
                    "The source has § 310^5(1) items 1-3 followed by a separate "
                    "subsection (2). Replay preserves that structure. Oracle "
                    "120122011010 drops source item 3 and also duplicates the "
                    "subsection (2) text into item 3."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/division:6/section:311/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay preserves the source/base terminal period in § 311(3). "
                    "Oracle 120122011010 differs only by omitting that terminal "
                    "period; no source amendment in this pair changes the sentence "
                    "body."
                ),
            ),
        )),
    ),
    ("112032019073", "115072023052"): EEPairResidualInventory(
        base_id="112032019073",
        oracle_id="115072023052",
        statute_title="Vereülekande tingimused ja kord",
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:3/section:10/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Amendment 115072023051 explicitly applies the case-inflected "
                    "replacement 'vereülekandeprotokoll' -> 'transfusiooniprotokoll' "
                    "to § 10(1), (2), (4), and (5). In § 10(1), the base source has "
                    "the inessive surface 'vereülekandeprotokollis', so replay "
                    "correctly materializes 'transfusiooniprotokollis'. Oracle "
                    "115072023052 instead has 'transfusiooniprotokolliks', which is "
                    "a different case form and is not source-backed by the visible "
                    "amendment clause."
                ),
            ),
        )),
    ),
    ("128092013010", "128062014175"): EEPairResidualInventory(
        base_id="128092013010",
        oracle_id="128062014175",
        statute_title="Kinnipidamiskeskuse sisekorraeeskirja kehtestamine",
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:2/section:5/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 128062014035 § 2 item 3 explicitly repeals § 5. "
                    "Replay therefore materializes § 5 as a repeal tombstone. Oracle "
                    "128062014175 nevertheless preserves the old § 5 heading as a "
                    "title-only subsection, without visible source text reactivating "
                    "the provision body."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:19/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 128062014035 § 2 item 5 explicitly repeals § 19. "
                    "Replay therefore materializes § 19 as a repeal tombstone. Oracle "
                    "128062014175 nevertheless preserves the old § 19 heading as a "
                    "title-only subsection, without visible source text reactivating "
                    "the provision body."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:21/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "The base § 21(3) text ends without a terminal period. Source act "
                    "128062014035 only applies the case-inflected global term rewrite "
                    "'migratsioonijärelevalveametnik' -> 'kinnipidamiskeskuse ametnik'. "
                    "Replay applies that rewrite and preserves the source punctuation; "
                    "oracle 128062014175 additionally appends a final period."
                ),
            ),
        )),
    ),
    ("108072011074", "127062017011"): EEPairResidualInventory(
        base_id="108072011074",
        oracle_id="127062017011",
        statute_title="Vabariigi Presidendi töökorra seadus",
        comparison_class="commensurable_delta",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:6/section:30",
                bucket="source_oracle_drift",
                evidence=(
                    "Base 108072011074 does not contain § 30 at all, and none of the "
                    "applied in-range amendments (106072012001, 112032015001, "
                    "127062017003) insert or rewrite a commencement section in chapter 6. "
                    "Oracle 127062017011 nevertheless carries § 30 titled "
                    "'Seaduse jõustumine'."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:30/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay has no source-backed text for chapter 6 § 30(1): the base "
                    "source lacks § 30, and the applied amendments only touch § 5. "
                    "Oracle 127062017011 still provides the unsourced commencement text "
                    "'Käesolev seadus jõustub 2001. aasta 1. septembril.'."
                ),
            ),
        )),
    ),
    ("110012014014", "108072016042"): EEPairResidualInventory(
        base_id="110012014014",
        oracle_id="108072016042",
        statute_title="Vanemahüvitise seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("118032011008", "111072013019"): EEPairResidualInventory(
        base_id="118032011008",
        oracle_id="111072013019",
        statute_title="Täiskasvanute koolituse seadus",
        comparison_class="forward_looking_oracle",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:2",
                bucket="source_oracle_drift",
                evidence=(
                    "The visible pair-delta sources only support the § 13 teacher-term "
                    "rewrites from 111072013001 and the § 16^1(4) insertion plus § 3(2) "
                    "and § 8(5) rewrites from 102072013001. They do not emit a generic "
                    "'haridus-ja teadusminister' -> 'valdkonna eest vastutav minister' "
                    "rewrite for § 6^1(2), so oracle 111072013019's broader chapter 2 "
                    "minister-title retitle and punctuation cleanup are oracle-side drift "
                    "relative to the visible pair delta."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6",
                bucket="source_oracle_drift",
                evidence=(
                    "No visible pair-delta source rewrites § 6(1) item 3 punctuation. "
                    "Oracle 111072013019 silently normalizes 'põhimääruse .' to "
                    "'põhimääruse.' without a supporting amendment clause."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "No visible pair-delta source rewrites the § 6(1) list text. Oracle "
                    "111072013019 nevertheless normalizes the final item punctuation from "
                    "'põhimääruse .' to 'põhimääruse.' without a supporting amendment "
                    "clause."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6/subsection:1/item:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Neither 102072013001 nor 111072013001 rewrites § 6(1) item 3. The "
                    "oracle still drops the space before the final period in "
                    "'põhimääruse .', which is editorial cleanup rather than visible "
                    "source-backed pair-delta text."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6_1",
                bucket="source_oracle_drift",
                evidence=(
                    "The only visible new source touching this pair, 111072013001 § 11, "
                    "rewrites only § 13(5^1) and § 13(5^2). Source 102072013001 rewrites "
                    "§ 3(2), § 8(5), and inserts § 16^1(4). Neither source emits a "
                    "generic minister-title rewrite for § 6^1, yet oracle 111072013019 "
                    "retitles § 6^1(2) to 'valdkonna eest vastutav minister'."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/section:6_1/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "No visible pair-delta amendment rewrites § 6^1(2). Oracle "
                    "111072013019 nevertheless changes 'haridus-ja teadusminister' to "
                    "'valdkonna eest vastutav minister', so this subsection drift is "
                    "oracle-side rather than source-backed."
                ),
            ),
            EEResidualRecord(
                address="chapter:5",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 102072013001 inserts § 16^1(4) with terminal period, but "
                    "oracle 111072013019 silently drops that final period. The chapter 5 "
                    "container mismatch is inherited from that oracle-side punctuation "
                    "cleanup, not from a visible pair-delta amendment."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:16_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 102072013001 inserts § 16^1(4) including the final "
                    "period. Oracle 111072013019 publishes the same sentence without the "
                    "period, so the § 16^1 container mismatch is editorial oracle drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:16_1/subsection:4",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 102072013001 inserts § 16^1(4) ending with "
                    "'kutsekeskharidusõpet.'. Oracle 111072013019 drops the final period, "
                    "so the subsection mismatch is an oracle-side punctuation cleanup "
                    "rather than a missing replay op."
                ),
            ),
        )),
    ),
    ("106012023046", "106012023047"): EEPairResidualInventory(
        base_id="106012023046",
        oracle_id="106012023047",
        statute_title="Mikrolülituse topoloogia kaitse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=cast(tuple[EEResidualRecord, ...], (
            EEResidualRecord(
                address="chapter:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 106012023002 rewrites only § 43(4) first sentence and "
                    "repeals only its second sentence. It does not remove the remaining "
                    "third sentence granting the applicant and owner free access to that "
                    "register file. Oracle 106012023047 nevertheless drops that third "
                    "sentence entirely, so the chapter-level mismatch is oracle-side drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:43",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 106012023002 item 15 rewrites only § 43(4) first sentence "
                    "and item 16 repeals only the second sentence. No source clause removes "
                    "the surviving third sentence about free access for the applicant and "
                    "owner. Oracle 106012023047 omits that third sentence anyway."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:43/subsection:4",
                bucket="source_oracle_drift",
                evidence=(
                    "After source act 106012023002, § 43(4) should still retain the third "
                    "sentence 'Taotlejale ja mikrolülituse topoloogia omanikule on oma "
                    "mikrolülituse topoloogia registritoimikuga tutvumine tasuta.'. The "
                    "source rewrites only the first sentence and repeals only the second. "
                    "Oracle 106012023047 drops the remaining third sentence without a "
                    "source-backed amendment."
                ),
            ),
        )),
    ),
    ("102102025017", "102102025018"): EEPairResidualInventory(
        base_id="102102025017",
        oracle_id="102102025018",
        statute_title="Atmosfääriõhu kaitse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(),
    ),
    ("131122013007", "131122013008"): EEPairResidualInventory(
        base_id="131122013007",
        oracle_id="131122013008",
        statute_title="Tarbijakaitseseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            EEResidualRecord(
                address="chapter:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 131122013001 item 9 rewrites only § 41^1(1), and its "
                    "quoted replacement text preserves a comma after '§ 49 lõikes 2^3' "
                    "before the later '§-des 54–55^1 ...' list. Oracle 131122013008 "
                    "changes that punctuation to a semicolon without a same-chain "
                    "source-backed amendment, so the chapter-level mismatch is inherited "
                    "oracle drift."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:41_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 131122013001 item 9 rewrites § 41^1(1) with the text "
                    "'..., § 49 lõikes 2^3, §-des 54–55^1 ...', keeping a comma after "
                    "'§ 49 lõikes 2^3'. Oracle 131122013008 instead publishes a semicolon "
                    "at that point, creating an unsourced punctuation drift in § 41^1."
                ),
            ),
            EEResidualRecord(
                address="chapter:6/section:41_1/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "The authoritative source payload in 131122013001 item 9 for "
                    "§ 41^1(1) contains a comma after '§ 49 lõikes 2^3'. Replay preserves "
                    "that source-backed punctuation, while oracle 131122013008 replaces "
                    "it with a semicolon without a same-chain amendment witness."
                ),
            ),
        ),
    ),
    ("123102025002", "123102025003"): EEPairResidualInventory(
        base_id="123102025002",
        oracle_id="123102025003",
        statute_title="Politsei ja piirivalve seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
                    "amendment chain. The new divisions 2^5 and 2^6 with §§ 7^65–7^68 "
                    "are source-backed by shared-chain source act 111032023004, but there "
                    "is no new pair-delta amendment between these two consolidated versions. "
                    "These insertions are therefore treated as same-chain/base-surface drift "
                    "rather than open replay-core work."
                ),
            )
        ),
    ),
    ("104072017126", "111012018009"): EEPairResidualInventory(
        base_id="104072017126",
        oracle_id="111012018009",
        statute_title="Autoveoseadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("102062020008", "114032025020"): EEPairResidualInventory(
        base_id="102062020008",
        oracle_id="114032025020",
        statute_title="Prokuratuuriseadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
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
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "The visible pair-delta sources between 102062020008 and "
                    "114032025020 are 106072023006 and 104012024001. They do not "
                    "emit a Prokuratuuriseadus-wide 'Justiitsministeerium' -> "
                    "'Justiits-ja Digiministeerium' rewrite, yet oracle 114032025020 "
                    "already carries that later ministry rename throughout § 1(1), "
                    "§ 9(1), § 43(2), and § 52(2)–(5). This row is therefore a "
                    "forward-looking oracle rename lane, not a replay-core defect."
                ),
            )
        ),
    ),
    ("125052012018", "121052014019"): EEPairResidualInventory(
        base_id="125052012018",
        oracle_id="121052014019",
        statute_title="Korteriomandiseadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("130122011047", "113122013023"): EEPairResidualInventory(
        base_id="130122011047",
        oracle_id="113122013023",
        statute_title="Sotsiaalhoolekande seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:3_1",
                    "chapter:3_1/section:21_3",
                    "chapter:3_1/section:21_3/subsection:2",
                    "chapter:3_1/section:21_3/subsection:2/item:7",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 125032011001 (Majandustegevuse seadustiku üldosa "
                    "seadus) rewrites the Sotsiaalhoolekande seadus tegevusloa "
                    "regime, including § 21^3 and its item 7 wording, but its "
                    "commencement section 137 delays those changes to 2014-01-01. "
                    "The comparison lane here is 2013-12-23, so replay correctly "
                    "keeps the pre-MajS version while oracle 113122013023 already "
                    "carries the future § 21^3 tegevusloa kontrolliese text."
                ),
            ),
            build_address_list_family(
                addresses=(
                    "chapter:4",
                    "chapter:4/section:22_1",
                    "chapter:4/section:22_1/subsection:3",
                    "chapter:4/section:22_1/subsection:3/item:2",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Oracle 113122013023 already includes the extra sentence in "
                    "§ 22^1(3) item 2 stating that undocumented income may be "
                    "confirmed by the applicant's signature. That wording is source-"
                    "backed only by later act 113122014002, whose commencement "
                    "section 8 delays the change to 2016-01-01. The comparison lane "
                    "here is 2013-12-23, so replay correctly excludes that future "
                    "sentence while the oracle shows it early."
                ),
            ),
            build_address_list_family(
                addresses=(
                    "chapter:4/section:23_1",
                    "chapter:4/section:23_1/subsection:5",
                    "chapter:4/section:23_1/subsection:5/item:1",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "In-range source act 106122012001 rewrites the pension-support "
                    "payment destination from 'arveldusarve' to 'arvelduskonto' in "
                    "§ 23^1(5) items 1 and 2. Replay now follows that source-backed "
                    "rewrite, but oracle 113122013023 still uses the divergent "
                    "'arveldukonto' form in item 1 and the parent subsection surface. "
                    "This remaining tail is therefore oracle-side wording drift, not a "
                    "current replay defect."
                ),
            ),
        ),
    ),
    ("120052025002", "120052025003"): EEPairResidualInventory(
        base_id="120052025002",
        oracle_id="120052025003",
        statute_title="Kohaliku omavalitsuse volikogu valimise seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:1",
                    "chapter:1/section:5",
                    "chapter:1/section:5/subsection:2",
                    "chapter:1/section:5/subsection:2/item:1",
                    "chapter:1/section:5_1",
                    "chapter:1/section:5_1/subsection:1",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Base 120052025002 and oracle 120052025003 expose the same visible "
                    "amendment chain with 45 identical amendment references. The § 5(2) "
                    "rewrite from the earlier 'välismaalane ... kes:' wording to the "
                    "condensed 'kodakondsuseta isik ...' form, and the omission of § 5^1, "
                    "are source-backed by shared-chain source act 120052025001 rather than "
                    "by a new pair-delta amendment between these consolidated versions. "
                    "These differences are treated as same-chain/base-surface drift."
                ),
            )
        ),
    ),
    ("112062025015", "112062025016"): EEPairResidualInventory(
        base_id="112062025015",
        oracle_id="112062025016",
        statute_title="Tööturumeetmete seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(),
    ),
    ("112062025011", "112062025012"): EEPairResidualInventory(
        base_id="112062025011",
        oracle_id="112062025012",
        statute_title="Riikliku pensionikindlustuse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:12",
                    "chapter:12/section:57",
                    "chapter:12/section:57/subsection:1",
                    "chapter:12/section:57/subsection:1/item:2",
                    "chapter:2",
                    "chapter:2/section:7",
                    "chapter:2/section:7/subsection:7",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Base 112062025011 and oracle 112062025012 expose the same visible "
                    "amendment chain with 93 identical amendment references, but replay "
                    "emits no same-chain operations for this comparison. Oracle "
                    "112062025012 nevertheless drops § 57(1) item 2 about "
                    "'väljateenitud aastate pensionide seaduses sätestatud pensionid' "
                    "and introduces the typo 'minsiter' in § 7(7) without a same-chain "
                    "amendment witness, so these differences are treated as same-chain "
                    "oracle-side drift."
                ),
            )
        ),
    ),
    ("109082022022", "108102024012"): EEPairResidualInventory(
        base_id="109082022022",
        oracle_id="108102024012",
        statute_title="Maagaasiseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:3/section:26_7/subsection:1",
                bucket="source_ambiguity",
                evidence=(
                    "Source act 102052024002 amends § 26^7(1) only by saying it is "
                    "supplemented after the word 'gaasivaru' with the phrase "
                    "'ning veeldatud maagaasi terminali haalamiskai ja taristu'. "
                    "Base 109082022022 contains two occurrences of 'gaasivaru' inside "
                    "§ 26^7(1), but the source does not say 'läbivalt' or otherwise "
                    "identify which occurrence(s) should be expanded. Replay therefore "
                    "has a real repeated-word source ambiguity here, while oracle "
                    "108102024012 effectively applies the insertion to both occurrences."
                ),
            ),
            EEResidualRecord(
                address="chapter:3/section:26_7/subsection:2/item:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 102052024002 rewrites § 26^7(2) p 6 to text ending with "
                    "a full stop: '... haalamiskai ja taristu haldamisega seotud kulud.'. "
                    "Replay preserves that source-backed terminal period, while oracle "
                    "108102024012 drops it."
                ),
            ),
        ),
    ),
    ("104112016005", "104012019016"): EEPairResidualInventory(
        base_id="104112016005",
        oracle_id="104012019016",
        statute_title="Ohvriabi seadus",
        comparison_class="commensurable_delta",
        residuals=(),
    ),
    ("111102024005", "111112025017"): EEPairResidualInventory(
        base_id="111102024005",
        oracle_id="111112025017",
        statute_title="Finantskriisi ennetamise ja lahendamise seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:6/division:3/section:59/subsection:5",
                bucket="source_pathology",
                evidence=(
                    "Authoritative RT XML for both base 111102024005 and oracle "
                    "111112025017 carries two sibling `<loigeNr>5</loigeNr>` nodes "
                    "under § 59: one exception-list subsection beginning "
                    "'Käesoleva paragrahvi lõikes 2 sätestatut ei kohaldata ...' and a "
                    "second `(5)` beginning 'Finantsinspektsioon võib omandaja "
                    "nõusolekul ...'. This same-label collision is source pathology; "
                    "current label-addressed PIT materialization can preserve only one "
                    "live subsection 5."
                ),
            ),
        ),
    ),
    ("128122018041", "104122019022"): EEPairResidualInventory(
        base_id="128122018041",
        oracle_id="104122019022",
        statute_title="Loomatauditõrje seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    bucket="source_oracle_drift",
                    records=(
                        (
                            "chapter:2/division:6",
                            "Base 128122018041 already carries repealed division 6 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Loomsete jäätmete käitlemine' anyway.",
                        ),
                        (
                            "chapter:2/division:7",
                            "Base 128122018041 already carries repealed division 7 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Loomade ja loomsete saaduste "
                            "sisse- ja väljavedu' anyway.",
                        ),
                        (
                            "chapter:2/division:8",
                            "Base 128122018041 already carries repealed division 8 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Veterinaartõendid' anyway.",
                        ),
                    ),
                )
            ),
        ),
    ),
    ("13247639", "131012012006"): EEPairResidualInventory(
        base_id="13247639",
        oracle_id="131012012006",
        statute_title="Jälitustegevuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *_lower_generated_residual_records(
                build_shortened_section_family(
                    records=(
                        (
                            "section:6/subsection:1/item:6",
                            "None of the executable in-range amendments (129122011001, "
                            "131012012005) touch § 6(1) p 6; replay preserves the base text with "
                            "terminal ';', while oracle 131012012006 normalizes it to '.'.",
                        ),
                        (
                            "section:10_1/subsection:1/item:3",
                            "None of the executable in-range amendments (129122011001, "
                            "131012012005) touch § 10^1(1) p 3; replay preserves the base text with "
                            "terminal ';', while oracle 131012012006 normalizes it to '.'.",
                        ),
                    ),
                ),
            ),
        ),
    ),
    ("119032013007", "112072014164"): EEPairResidualInventory(
        base_id="119032013007",
        oracle_id="112072014164",
        statute_title="Ühistranspordiseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:10/section:53_5/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 113032014004 replaces chapter 10 effective 2014-07-01 "
                    "and carries § 53^5(2) with a terminal period. Replay preserves that "
                    "source punctuation, while oracle 112072014164 has the same subsection "
                    "text without the terminal period."
                ),
            ),
        ),
    ),
    ("118122012017", "123122022029"): EEPairResidualInventory(
        base_id="118122012017",
        oracle_id="123122022029",
        statute_title="Kommertspandiseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/section:7/subsection:1_2",
                bucket="source_oracle_drift",
                evidence=(
                    "The only in-range 2022 source clause targeting § 7 is source act "
                    "123122022002, which rewrites § 7(1^1). None of the applied source "
                    "acts inserts or amends § 7(1^2), but oracle 123122022029 still "
                    "carries that extra subsection."
                ),
            ),
        ),
    ),
    ("115032022004", "101072025003"): EEPairResidualInventory(
        base_id="115032022004",
        oracle_id="101072025003",
        statute_title="Eesti Vabariigi haridusseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="section:36_6/subsection:2_2/item:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 109012025001 explicitly rewrites § 36^6(2^2) p 1 to the "
                    "replay-side shorter text 'õppijate, õpetajate, lapsehoidjate ja "
                    "akadeemiliste töötajate kohta;'. Oracle 101072025003 instead keeps "
                    "the broader enumeration introduced by 123122024001, so the remaining "
                    "difference is source-backed replay versus oracle carry-forward drift."
                ),
            ),
        ),
    ),
    ("112112021018", "107012025003"): EEPairResidualInventory(
        base_id="112112021018",
        oracle_id="107012025003",
        statute_title="Töötajate usaldusisiku seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:4/section:15_1 2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 107012025001 explicitly inserts the § 15^1 directive note "
                    "as a separate normitehniline märkus ('paragrahvi 15 1 2) seadust "
                    "täiendatakse normitehnilise märkusega ...'). Replay preserves that "
                    "source-backed note, while oracle 107012025003 omits it."
                ),
            ),
        ),
    ),
    ("115032014084", "101092015036"): EEPairResidualInventory(
        base_id="115032014084",
        oracle_id="101092015036",
        statute_title="Üleliigse laovaru tasu seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    records=(
                        (
                            "section:23/subsection:1",
                            "Source act 130062015004 is a generic ministry reorganization "
                            "rename and emits the statute-wide text replacement "
                            "'Põllumajandusministeerium' -> 'Maaeluministeerium'. Replay preserves "
                            "that source-backed rename in § 23(1), while oracle 101092015036 "
                            "retains the older ministry name.",
                        ),
                        (
                            "section:23/subsection:4",
                            "Source act 130062015004 emits the same generic "
                            "'Põllumajandusministeerium' -> 'Maaeluministeerium' rename for the "
                            "whole statute. Replay updates § 23(4) accordingly, while oracle "
                            "101092015036 keeps 'Põllumajandusministeerium'.",
                        ),
                    ),
                )
            ),
        ),
    ),
    ("109042021007", "114032025016"): EEPairResidualInventory(
        base_id="109042021007",
        oracle_id="114032025016",
        statute_title="Kohtutäituri seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    records=(
                        (
                            "chapter:2/division:2/section:13/subsection:1",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                        (
                            "chapter:2/division:2/section:13/subsection:2",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                        (
                            "chapter:3/division:1/section:78/subsection:1/item:16",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                        (
                            "chapter:3/division:2/section:92/subsection:1/item:2",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                    ),
                )
            ),
            EEResidualRecord(
                address="chapter:2/division:5/section:30/subsection:2_1",
                bucket="source_pathology",
                evidence=(
                    "Source act 109042021001 emits inserted § 30(2^1) cleanly, but base "
                    "109042021007 already cites that act in its own amendment refs while still "
                    "omitting the inserted subsection. Oracle 114032025016 contains it."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/division:5/section:31/subsection:1_1",
                bucket="source_pathology",
                evidence=(
                    "Source act 109042021001 emits inserted § 31(1^1) cleanly, but base "
                    "109042021007 already cites that act in its own amendment refs while still "
                    "omitting the inserted subsection. Oracle 114032025016 contains it."
                ),
            ),
            EEResidualRecord(
                address="chapter:2/division:5/section:31/subsection:2/item:5",
                bucket="source_pathology",
                evidence=(
                    "Source act 109042021001 expands § 31(2) p 5 with the replay-side tail, "
                    "but base 109042021007 already cites that act while keeping the older text. "
                    "Oracle 114032025016 carries the 109042021001 wording."
                ),
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:2/division:5/section:37_2",
                            "Source act 109042021001 emits inserted § 37^2 cleanly, but base "
                            "109042021007 already cites that act while omitting the entire section. "
                            "Oracle 114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:1",
                            "Source act 109042021001 emits § 37^2(1) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:2",
                            "Source act 109042021001 emits § 37^2(2) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3",
                            "Source act 109042021001 emits § 37^2(3) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3/item:1",
                            "Source act 109042021001 emits § 37^2(3) p 1 cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3/item:2",
                            "Source act 109042021001 emits § 37^2(3) p 2 cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:4",
                            "Source act 109042021001 emits § 37^2(4) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                    ),
                )
            ),
            EEResidualRecord(
                address="chapter:2/division:5/section:37_3/subsection:1",
                bucket="source_pathology",
                evidence=(
                    "Source act 116122022001 emits the inserted § 37^3 payload with a nested "
                    "amendment wrapper and trailing quote-semicolon residue ('14 1) ... koda.”;'), "
                    "so the replay-side mismatch is source malformedness rather than open EE semantics."
                ),
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:2/division:5/section:40_1",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment heading "
                            "'elektroonilise arestimissüsteemi kaudu'; oracle 114032025016 carries the "
                            "109042021001 'täitmisregistri kaudu' text.",
                        ),
                        (
                            "chapter:2/division:5/section:40_1/subsection:1",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment subsection text for § 40^1(1); "
                            "oracle 114032025016 carries the 109042021001 wording.",
                        ),
                        (
                            "chapter:2/division:5/section:40_1/subsection:2",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment subsection text for § 40^1(2); "
                            "oracle 114032025016 carries the 109042021001 wording.",
                        ),
                    ),
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_section_omission_family(
                    section_address="chapter:2/division:2/section:15_1",
                    section_symbol="§ 15^1",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    subsection_labels=("1", "2", "3", "4", "5", "6"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_item_omission_family(
                    item_address="chapter:2/division:2/section:15_1/subsection:1",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    item_labels=("1", "2", "3", "4", "5"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_item_omission_family(
                    item_address="chapter:2/division:2/section:15_1/subsection:5",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    item_labels=("1", "2", "3", "4", "5", "6"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_section_omission_family(
                    section_address="chapter:2/division:2/section:15_2",
                    section_symbol="§ 15^2",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    subsection_labels=("1", "2", "3", "4"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_item_omission_family(
                    item_address="chapter:2/division:2/section:15_2/subsection:2",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    item_labels=("1", "2", "3", "4"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_section_omission_family(
                    section_address="chapter:2/division:2/section:15_3",
                    section_symbol="§ 15^3",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    subsection_labels=("1", "2", "3", "4"),
                    bucket="source_oracle_drift",
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_item_omission_family(
                    item_address="chapter:2/division:2/section:15_3/subsection:2",
                    source_act_id="120062022001",
                    oracle_id="114032025016",
                    item_labels=("1", "2", "3", "4", "5"),
                    bucket="source_oracle_drift",
                )
            ),
        ),
    ),
    ("106052020036", "103022026013"): EEPairResidualInventory(
        base_id="106052020036",
        oracle_id="103022026013",
        statute_title="Riigisaladuse ja salastatud välisteabe seadus",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
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
            ),
        ),
    ),
    ("107032012004", "101062013014"): EEPairResidualInventory(
        base_id="107032012004",
        oracle_id="101062013014",
        statute_title="Rahvusvahelise sõjalise koostöö seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
                    "101062013014 is source act 101062013001, which emits only two "
                    "target ops for this statute: one new third sentence in § 21(2) "
                    "and one text insertion in § 23(2). It emits no generic "
                    "'kaitseminister' -> 'valdkonna eest vastutav minister' rewrite "
                    "family for Rahvusvahelise sõjalise koostöö seadus, so oracle "
                    "101062013014 carries a broader forward-looking minister-title "
                    "retitle than the visible pair delta supports."
                ),
            )
        ),
    ),
    ("115032013034", "125012017006"): EEPairResidualInventory(
        base_id="115032013034",
        oracle_id="125012017006",
        statute_title="Asjaõigusseaduse rakendamise seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("119122024003", "118122025016"): EEPairResidualInventory(
        base_id="119122024003",
        oracle_id="118122025016",
        statute_title="Ettevõtlustulu lihtsustatud maksustamise seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("129112010006", "129112010007"): EEPairResidualInventory(
        base_id="129112010006",
        oracle_id="129112010007",
        statute_title="Ettevõtluse toetamise ja laenude riikliku tagamise seadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("106032026008", "106032026009"): EEPairResidualInventory(
        base_id="106032026008",
        oracle_id="106032026009",
        statute_title="Reklaamiseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            EEResidualRecord(
                address="chapter:4/section:29/subsection:3_7",
                bucket="source_oracle_drift",
                evidence=(
                    "Base 106032026008 and oracle 106032026009 are same-chain "
                    "tervikteksts for Reklaamiseadus with grupi_id 320219 and no "
                    "visible applied amendment delta between them. Base 106032026008 "
                    "still carries § 29(3^7) ('Hoiu-laenuühistu reklaam ei tohi "
                    "sisaldada pakutava hoiuseintressi määra.'), while the later "
                    "oracle 106032026009 omits that subsection entirely, so the tail "
                    "is editorial oracle drift rather than a replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("106032026004", "106032026005"): EEPairResidualInventory(
        base_id="106032026004",
        oracle_id="106032026005",
        statute_title="Avaliku teabe seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            *(
                EEResidualRecord(
                    address=address,
                    bucket="source_oracle_drift",
                    evidence=(
                        "Base 106032026004 and oracle 106032026005 are same-chain "
                        "tervikteksts for Avaliku teabe seadus with grupi_id 162383 and "
                        "no visible applied amendment delta between them. Base "
                        "106032026004 still carries the official e-mail address list "
                        "items under § 32^1, while the later oracle 106032026005 omits "
                        "those two items entirely, so the tail is editorial oracle drift "
                        "rather than a replay-side amendment bug."
                    ),
                )
                for address in (
                    "chapter:4/division:2/section:32_1/subsection:1_2/item:7",
                    "chapter:4/division:2/section:32_1/subsection:2_1/item:5",
                )
            ),
        ),
    ),
    ("108102024023", "108102024024"): EEPairResidualInventory(
        base_id="108102024023",
        oracle_id="108102024024",
        statute_title="Vedelkütuse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            EEResidualRecord(
                address="chapter:1_1/section:2_4/subsection:1_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Base 108102024023 and oracle 108102024024 are same-chain "
                    "tervikteksts for Vedelkütuse seadus with grupi_id 156253 and no "
                    "visible applied amendment delta between them. Oracle 108102024024 "
                    "adds § 2^4(1^1) on Keskkonnaameti data exchange with the EU "
                    "database, while the later terviktekst itself yields no parsed "
                    "amendment op introducing that subsection, so the tail is editorial "
                    "oracle drift rather than a replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("107012026023", "107012026024"): EEPairResidualInventory(
        base_id="107012026023",
        oracle_id="107012026024",
        statute_title="Pakendiseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            *(
                EEResidualRecord(
                    address=address,
                    bucket="source_oracle_drift",
                    evidence=(
                        "Base 107012026023 and oracle 107012026024 are same-chain "
                        "tervikteksts for Pakendiseadus with grupi_id 157649 and no "
                        "visible applied amendment delta between them. The later "
                        "oracle 107012026024 adds the taaskasutusorganisatsiooni "
                        "owner-restriction paragraphs under §§ 10^1(1^1) and 17^2(4), "
                        "while the later terviktekst itself yields no parsed amendment "
                        "ops introducing them, so the tail is editorial oracle drift "
                        "rather than a replay-side amendment bug."
                    ),
                )
                for address in (
                    "chapter:1/section:10_1/subsection:1_1",
                    "chapter:3/section:17_2/subsection:4",
                )
            ),
        ),
    ),
    ("102102025004", "102102025005"): EEPairResidualInventory(
        base_id="102102025004",
        oracle_id="102102025005",
        statute_title="Maksukorralduse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(
            EEResidualRecord(
                address="chapter:1/division:3_1/section:25_5/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Base 102102025004 and oracle 102102025005 are same-chain "
                    "tervikteksts for Maksukorralduse seadus with grupi_id 154022 "
                    "and no visible applied amendment delta between them. Base "
                    "102102025004 still says 'elamislubade ja töölubade registri' in "
                    "§ 25^5(1), while the later oracle 102102025005 silently rewrites "
                    "that to 'elamislubade ja elamisõiguste andmekogu', so the tail is "
                    "editorial oracle drift rather than a replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("101072023008", "101072023009"): EEPairResidualInventory(
        base_id="101072023008",
        oracle_id="101072023009",
        statute_title="Alkoholi-, tubaka-, kütuse- ja elektriaktsiisi seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("107012026012", "107012026013"): EEPairResidualInventory(
        base_id="107012026012",
        oracle_id="107012026013",
        statute_title="Põhikooli- ja gümnaasiumiseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("126062025020", "126062025021"): EEPairResidualInventory(
        base_id="126062025020",
        oracle_id="126062025021",
        statute_title="Vangistusseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:1",
                    "chapter:1/section:5_4",
                    "chapter:1/section:5_4/subsection:5",
                    "chapter:1/section:5_4/subsection:5/item:6",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Base 126062025020 and oracle 126062025021 expose the same visible "
                    "amendment chain with 97 identical amendment references. Replay emits "
                    "the one same-chain operation carried by source act 131122024004, but "
                    "oracle 126062025021 nevertheless rewrites the surviving "
                    "§ 5^4(5) item 6 ministry wording from 'Justiitsministeeriumi' to "
                    "'Justiits-ja Digiministeeriumi' without a same-chain amendment "
                    "witness, so this tail is treated as same-chain oracle-side drift."
                ),
            )
        ),
    ),
    ("107012026014", "107012026015"): EEPairResidualInventory(
        base_id="107012026014",
        oracle_id="107012026015",
        statute_title="Kutseõppeasutuse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
                    "chapter:7",
                    "chapter:7/section:38",
                    "chapter:7/section:38/subsection:6",
                    "chapter:7/section:38/subsection:7",
                    "chapter:7/section:38/subsection:8",
                    "chapter:7/section:40",
                    "chapter:7/section:40/subsection:3",
                    "chapter:7/section:40/subsection:3/item:1",
                    "chapter:7/section:40/subsection:3/item:2",
                    "chapter:7/section:40/subsection:4",
                    "chapter:7/section:40/subsection:5",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Base 107012026014 and oracle 107012026015 expose the same visible "
                    "amendment chain with 21 identical amendment references. The new "
                    "teacher career-stage subsections in § 38 and the teacher pay "
                    "subsections in § 40 are source-backed by shared-chain source act "
                    "107012026003 rather than by a new pair-delta amendment between "
                    "these consolidated versions. These differences are treated as "
                    "same-chain/base-surface drift."
                ),
            )
        ),
    ),
    ("121052014030", "121052014031"): EEPairResidualInventory(
        base_id="121052014030",
        oracle_id="121052014031",
        statute_title="Euroopa Liidu ühise põllumajanduspoliitika rakendamise seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *(
                EEResidualRecord(
                    address=address,
                    bucket="source_oracle_drift",
                    evidence=(
                        "The only new amendment reference between 121052014030 and "
                        "121052014031 is source act 129062014109, which emits only the "
                        "generic minister-title substitutions '...minister' -> "
                        "'valdkonna eest vastutav minister' for this statute. It emits no "
                        "operation dropping 'riiklikku' from § 94(6^3) or adding 'riiklikku' "
                        "to § 95(3), so oracle 121052014031 carries unsupported supervision "
                        "wording drift relative to the visible pair delta."
                    ),
                )
                for address in (
                    "chapter:16",
                    "chapter:16/section:94",
                    "chapter:16/section:94/subsection:6_3",
                    "chapter:16/section:95",
                    "chapter:16/section:95/subsection:3",
                )
            ),
            *(
                EEResidualRecord(
                    address=address,
                    bucket="source_oracle_drift",
                    evidence=(
                        "The only new amendment reference between 121052014030 and "
                        "121052014031 is source act 129062014109, which emits only the "
                        "generic minister-title substitutions '...minister' -> "
                        "'valdkonna eest vastutav minister' for this statute. It emits no "
                        "operation changing § 31(1) terminal punctuation, so oracle "
                        "121052014031's doubled period after 'toodete loetelu' is treated as "
                        "oracle-surface punctuation drift."
                    ),
                )
                for address in (
                    "chapter:5",
                    "chapter:5/division:3",
                    "chapter:5/division:3/section:31",
                    "chapter:5/division:3/section:31/subsection:1",
                )
            ),
        ),
    ),
    ("126042013006", "111042014003"): EEPairResidualInventory(
        base_id="126042013006",
        oracle_id="111042014003",
        statute_title="Kindlustustegevuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
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
                    ),
                )
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
                    records=(
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
            ),
            EEResidualRecord(
                address="chapter:11/division:3/section:187",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 112072013002 only repeals the whole 11. peatüki 3. jagu. "
                    "Replay keeps the repealed child section as a title-only stub "
                    "'Finantskonglomeraat', while the oracle renders the same repeal as an "
                    "empty section wrapper with a dash subsection."
                ),
            ),
            EEResidualRecord(
                address="chapter:11/division:3/section:187/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 112072013002 only repeals the whole 11. peatüki 3. jagu. "
                    "The oracle materializes section 187 as subsection 1 with a lone dash, "
                    "while replay keeps a childless repeal stub at section level."
                ),
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
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
            ),
            EEResidualRecord(
                address="chapter:9/division:3/section:125/subsection:1/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay matches the source-side capitalization 'Liikluskindlustuse seaduse'; "
                    "oracle lowercases it to 'liikluskindlustuse seaduse'."
                ),
            ),
        ),
    ),
    ("120022015005", "109012018006"): EEPairResidualInventory(
        base_id="120022015005",
        oracle_id="109012018006",
        statute_title="Alkoholiseadus",
        comparison_class="forward_looking_oracle",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
                addresses=(
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
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 109012018002 inserts the added § 40 and § 42 retail-"
                    "restriction text, including § 40(1^2), § 40(1^3), § 40(2^1), "
                    "§ 40(4), and § 42(1) item 3. Its commencement section 3 delays "
                    "§ 84 point 2 and § 84 point 4 to 2018-06-01, and delays § 84 "
                    "point 1 and § 84 point 3 to 2019-06-01. The comparison lane "
                    "here is 2018-01-19, so replay correctly excludes that future "
                    "text while oracle 109012018006 already carries the later § 40 / "
                    "§ 42 restrictions."
                ),
            )
        ),
    ),
    ("105122014039", "114032025013"): EEPairResidualInventory(
        base_id="105122014039",
        oracle_id="114032025013",
        statute_title="Abieluvararegistri seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:6/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 109052017001 temporarily adds the first sentence "
                    "'Registritoimikuga tutvumise loa annab notar.', but later source act "
                    "121122016001 rewrites § 6(3) back to the replay-side shorter wording. "
                    "The oracle keeps the earlier 2017 text."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:42_5/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay follows the later source-side ministry rename "
                    "'Justiits- ja Digiministeerium', while the oracle keeps the older "
                    "'Justiitsministeerium' wording in § 42^5(1)."
                ),
            ),
        ),
    ),
    ("108112012002", "103072014023"): EEPairResidualInventory(
        base_id="108112012002",
        oracle_id="103072014023",
        statute_title="Kalapüügiseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/section:13_4/subsection:15_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 103072014018 inserts § 13^4(15^1) with the replay-side "
                    "terminal full stop, while oracle 103072014023 differs only by "
                    "dropping that final punctuation."
                ),
            ),
            EEResidualRecord(
                address="chapter:3_1/section:23_2/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 103072014018 rewrites § 23^2 with the replay-side terminal "
                    "full stop in subsection 2; oracle 103072014023 differs only by "
                    "dropping that final punctuation."
                ),
            ),
            EEResidualRecord(
                address="chapter:4/section:25_1/subsection:14",
                bucket="source_oracle_drift",
                evidence=(
                    "Later source act 129062014109 globally rewrites minister titles to the "
                    "replay-side generic 'valdkonna eest vastutav minister', while oracle "
                    "103072014023 keeps the older specific 'keskkonnaministri' wording in "
                    "§ 25^1(14)."
                ),
            ),
        ),
    ),
    ("193936", "13336397"): EEPairResidualInventory(
        base_id="193936",
        oracle_id="13336397",
        statute_title="Liiklusseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:6/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 12776187 explicitly rewrites § 6(2) to the replay-side text "
                    "with 'lasteaed-algkoolid, algkoolid'; oracle omits those schools."
                ),
            ),
            EEResidualRecord(
                address="chapter:12/section:64/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 919171 explicitly carries the replay-side wording "
                    "'viie tööpäeval jooksul'; oracle has 'viie tööpäeva jooksul'."
                ),
            ),
            EEResidualRecord(
                address="chapter:13/section:70/subsection:4/item:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 13244294 only rewrites item 1; no parsed source amendment in "
                    "the visible chain supports the oracle-side shortening to 'päästetöö tegijatelt'."
                ),
            ),
            EEResidualRecord(
                address="chapter:14/section:72/subsection:4",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 919171 rewrites § 72(5), not § 72(4); oracle contains a larger "
                    "replacement regime for subsection 4 not supported by the visible parsed chain."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:21/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay matches the base/source-side text '(edaspidi juhtimisõigus)'; "
                    "oracle has '(edaspidi juhtimisõiguse)'."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:25/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Replay matches the base/source-side reference '§ 28 lõike 2'; oracle points to '§ 28^1 lõike 4'."
                ),
            ),
            EEResidualRecord(
                address="chapter:15/section:79/subsection:4",
                bucket="appendix_display_pathology",
                evidence=(
                    "Replay-side material follows the source appendix/body chain; oracle adds "
                    "parking-card display text such as 'Esikülg', 'Tagakülg', dimensions, and "
                    "background color without matching support in the checked source chain."
                ),
            ),
        ),
    ),
    ("127122013026", "129102014007"): EEPairResidualInventory(
        base_id="127122013026",
        oracle_id="129102014007",
        statute_title="Riigilõivuseadus",
        comparison_class="forward_looking_oracle",
        residuals=cast(tuple[EEResidualRecord, ...], (
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_shortened_section_family(
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
            ),
            *build_address_list_family(
                addresses=(
                    "part:2",
                    "part:2/chapter:3",
                    "part:2/chapter:3/section:22",
                    "part:2/chapter:3/section:22/subsection:1",
                    "part:2/chapter:3/section:22/subsection:1/item:2",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121062014008 (Kohtute seaduse ja sellega "
                    "seonduvalt teiste seaduste muutmise seadus) adds the "
                    "extra 'ning muutmise' wording to Riigilõivuseadus "
                    "§ 22(1) p 2, but its commencement section 30 delays the "
                    "Riigilõivuseadus changes to 2015-01-01. The comparison lane "
                    "here is 2014-12-01, so replay correctly excludes that future "
                    "§ 22 wording while oracle 129102014007 already carries it."
                ),
            ),
            EEResidualRecord(
                address="part:2/chapter:3/section:23/subsection:1/item:6_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121032014003 inserts item 6^1 cleanly; replay materializes it, "
                    "but oracle 129102014007 omits it."
                ),
            ),
            EEResidualRecord(
                address="part:2/chapter:3/section:40/subsection:1/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Base 127122013026 already carries the replay-side "
                    "'siseminister või kaitseminister' wording, and no parsed "
                    "amendment in the checked chain rewrites that item; oracle "
                    "129102014007 reshapes it to the generic "
                    "'valdkonna eest vastutavad ministrid' wording."
                ),
            ),
            *build_address_list_family(
                addresses=(
                    "part:3",
                    "part:3/chapter:5",
                    "part:3/chapter:5/division:1",
                    "part:3/chapter:5/division:1/section:57",
                    "part:3/chapter:5/division:1/section:57/subsection:2",
                    "part:3/chapter:5/division:1/section:57/subsection:3",
                    "part:3/chapter:5/division:1/section:57/subsection:4",
                    "part:3/chapter:5/division:1/section:57/subsection:5",
                    "part:3/chapter:5/division:1/section:57/subsection:7",
                    "part:3/chapter:5/division:1/section:57/subsection:8",
                    "part:3/chapter:5/division:1/section:57/subsection:9",
                    "part:3/chapter:5/division:1/section:57/subsection:10",
                    "part:3/chapter:5/division:1/section:57/subsection:11",
                    "part:3/chapter:5/division:1/section:57/subsection:12",
                    "part:3/chapter:5/division:1/section:57/subsection:13",
                    "part:3/chapter:5/division:1/section:57/subsection:14",
                    "part:3/chapter:5/division:1/section:57/subsection:15",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121062014008 rewrites the Riigilõivuseadus § 57 "
                    "subsection cluster and removes the old e-toimik sentence tails "
                    "from § 57(13) and § 57(15), but its commencement section 30 "
                    "delays those Riigilõivuseadus changes to 2015-01-01. The "
                    "comparison lane here is 2014-12-01, so replay correctly keeps "
                    "the pre-2015 § 57 wording while oracle 129102014007 already "
                    "carries the later subsection text."
                ),
            ),
            *build_address_list_family(
                addresses=(
                    "part:3/chapter:5/division:2/section:64_1",
                    "part:3/chapter:5/division:2/section:64_1/subsection:1",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 113032014003 (Korteriomandi- ja "
                    "korteriühistuseadus) inserts Riigilõivuseadus § 64^1, but its "
                    "commencement section 95 delays that insertion to 2018-01-01. "
                    "The comparison lane here is 2014-12-01, so replay correctly "
                    "excludes the future § 64^1 text while oracle 129102014007 "
                    "already carries it."
                ),
            ),
            *build_address_list_family(
                addresses=(
                    "part:4",
                    "part:4/chapter:19",
                    "part:4/chapter:19/section:339",
                    "part:4/chapter:19/section:339/subsection:1",
                    "part:4/chapter:19/section:340",
                    "part:4/chapter:19/section:340/subsection:4",
                ),
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 113032014003 rewrites Riigilõivuseadus § 339 and "
                    "§ 340(4) to the later korteriomandi / kaasomandi-osa wording, "
                    "but its commencement section 95 delays those changes to "
                    "2018-01-01. The comparison lane here is 2014-12-01, so replay "
                    "correctly keeps the pre-2018 wording while oracle 129102014007 "
                    "already carries the later § 339 / § 340(4) text."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:2/section:247",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 247, yet oracle "
                    "129102014007 presents it as a repealed shell while replay preserves the "
                    "base/source-side title surface."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:2/section:247/subsection:1",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 247(1), yet oracle "
                    "129102014007 presents it as a repealed shell while replay has no source-backed "
                    "reason to emit that subsection stub."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:4/section:258",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 258, yet oracle "
                    "129102014007 blanks it as a repealed shell while replay preserves the "
                    "base/source-side section title."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:4/section:258/subsection:1",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 258(1), yet oracle "
                    "129102014007 renders it as a repealed shell."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:4/section:259",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 259, but replay keeps "
                    "the base/source-side section while oracle 129102014007 omits it."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:4/section:260",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 260, but replay keeps "
                    "the base/source-side section while oracle 129102014007 omits it."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:11/division:4/section:261",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 261, but replay keeps "
                    "the base/source-side section while oracle 129102014007 omits it."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:14/section:308",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 308, yet oracle "
                    "129102014007 presents it as a repealed shell while replay preserves the "
                    "base/source-side empty-title section stub."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:14/section:308/subsection:1",
                bucket="source_pathology",
                evidence=(
                    "No parsed amendment in the applied 2014 chain targets § 308(1), yet oracle "
                    "129102014007 renders it as a repealed shell."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:5/division:1/section:57/subsection:15_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121052014001 inserts § 57(15^1) cleanly; replay materializes it, "
                    "but oracle 129102014007 omits it."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:5/division:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121062014008 explicitly retitles the division to "
                    "'Tartu Maakohtu registri- ja kinnistusosakonna toimingud'; "
                    "oracle 129102014007 keeps the older generic 'Kohtu ...' heading."
                ),
            ),
            EEResidualRecord(
                address="part:3/chapter:5/division:2/section:73/subsection:6",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121062014008 explicitly repeals § 73(6); replay applies that repeal, "
                    "but oracle 129102014007 still carries live subsection text."
                ),
            ),
            EEResidualRecord(
                address="part:5/chapter:20/section:357/subsection:2",
                bucket="appendix_display_pathology",
                evidence=(
                    "Base 127122013026 embeds source-side appendix HTML under § 357 while oracle "
                    "129102014007 exposes appendix material through the out-of-body lisaViide lane. "
                    "Replay preserves the in-body appendix marker, so this is an appendix display "
                    "projection mismatch rather than a source-backed legal text mutation."
                ),
            ),
            EEResidualRecord(
                address="part:5/chapter:20/section:357/subsection:3",
                bucket="appendix_display_pathology",
                evidence=(
                    "Base 127122013026 embeds source-side appendix HTML under § 357 while oracle "
                    "129102014007 exposes appendix material through the out-of-body lisaViide lane. "
                    "Replay preserves the in-body appendix table text, so this is an appendix display "
                    "projection mismatch rather than a source-backed legal text mutation."
                ),
            ),
        )),
    ),
    ("119032013003", "131122025003"): EEPairResidualInventory(
        base_id="119032013003",
        oracle_id="131122025003",
        statute_title="Eesti territooriumi haldusjaotuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/section:7/subsection:7_1/item:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121062016001 inserts § 7(7^1) p 1 with terminal punctuation; "
                    "replay preserves that source-side semicolon, while oracle 131122025003 "
                    "differs only by dropping it."
                ),
            ),
        ),
    ),
    ("106082022030", "131122024023"): EEPairResidualInventory(
        base_id="106082022030",
        oracle_id="131122024023",
        statute_title="Lastekaitseseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:4/section:20/subsection:7",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 131122024003 inserts § 20(7) with "
                    "'Justiitsministeeriumi', while oracle 131122024023 carries the "
                    "later ministry-name drift 'Justiits-ja Digiministeeriumi'."
                ),
            ),
        ),
    ),
    ("104072024023", "131122024050"): EEPairResidualInventory(
        base_id="104072024023",
        oracle_id="131122024050",
        statute_title="Võlaõigusseaduse, tsiviilseadustiku üldosa seaduse ja rahvusvahelise eraõiguse seaduse rakendamise seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:9_2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 131122024005 explicitly inserts a standalone normitehniline "
                    "märkus at § 9^2 ('paragrahvi 9 2) seadust täiendatakse "
                    "normitehnilise märkusega ...'). Replay preserves that source-backed "
                    "directive note, while oracle 131122024050 omits it."
                ),
            ),
        ),
    ),
    ("120062022025", "123122022031"): EEPairResidualInventory(
        base_id="120062022025",
        oracle_id="123122022031",
        statute_title="Sihtasutuste seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/section:14/subsection:1/item:10_1",
                bucket="source_oracle_drift",
                evidence=(
                    "Neither applied amendment touching Sihtasutuste seadus "
                    "(105052022001, 123122022002) targets § 14(1) p 10^1. Replay preserves "
                    "the base/source terminal semicolon, while oracle 123122022031 changes "
                    "only the final punctuation to a period."
                ),
            ),
        ),
    ),
    ("130122025021", "130122025022"): EEPairResidualInventory(
        base_id="130122025021",
        oracle_id="130122025022",
        statute_title="Käibemaksuseadus",
        comparison_class="commensurable_delta",
        residuals=_lower_generated_residual_records(
            build_address_list_family(
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
        ),
    ),
    ("108072025061", "107012026021"): EEPairResidualInventory(
        base_id="108072025061",
        oracle_id="107012026021",
        statute_title="Keskkonnatasude seadus",
        comparison_class="commensurable_delta",
        residuals=tuple(
            EEResidualRecord(
                address=address,
                bucket="source_pathology",
                evidence=(
                    "The sole new amendment reference between 108072025061 and "
                    "107012026021 is 107012026004, and replay applies only that act "
                    "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                    "§ 4 block compiles to 20 ops covering the early 2026 "
                    "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                    "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                    "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                    "nonetheless introduces the replay-diverging tuuleenergiast "
                    "elektrienergia tootmise tasu cluster at this address."
                ),
            )
            for address in (
                "chapter:11/section:68_5/subsection:4",
                "chapter:11/section:68_5/subsection:5",
                "chapter:3_1/section:21_2/subsection:2",
                "chapter:3_1/section:21_2/subsection:2_1",
                "chapter:3_1/section:21_5",
                "chapter:3_1/section:21_5/subsection:1",
                "chapter:3_1/section:21_5/subsection:2",
                "chapter:3_1/section:21_5/subsection:3",
                "chapter:5/section:32/subsection:10",
                "chapter:5/section:32/subsection:8",
                "chapter:5/section:32/subsection:9",
                "chapter:8/section:55_3/subsection:1",
                "chapter:8/section:55_3/subsection:10",
                "chapter:8/section:55_3/subsection:2",
                "chapter:8/section:55_3/subsection:2_1",
                "chapter:8/section:55_3/subsection:2_2",
                "chapter:8/section:55_3/subsection:3",
            )
        ),
    ),
    ("104122024013", "128012026005"): EEPairResidualInventory(
        base_id="104122024013",
        oracle_id="128012026005",
        statute_title="Looduskaitseseadus",
        comparison_class="commensurable_delta",
        residuals=tuple(
            EEResidualRecord(
                address=address,
                bucket="source_pathology",
                evidence=(
                    "The only in-range amendments between 104122024013 and "
                    "128012026005 are 112072025001 and 128012026001. Replay "
                    "applies exactly those acts at the oracle cutoff 2026-02-07, "
                    "and they compile only the § 68^3 p 1 repeal plus the new "
                    "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                    "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                    "nonetheless blanks or omits that entire mingi/kähriku farm "
                    "cluster at this address."
                ),
            )
            for address in (
                "chapter:8/section:57/subsection:6",
                "chapter:8/section:57/subsection:7",
                "chapter:8/section:57_1",
                "chapter:8/section:57_1/subsection:1",
                "chapter:8/section:57_1/subsection:1/item:1",
                "chapter:8/section:57_1/subsection:1/item:2",
                "chapter:8/section:57_1/subsection:1/item:3",
                "chapter:8/section:57_2",
                "chapter:8/section:57_2/subsection:1",
                "chapter:8/section:57_2/subsection:2",
                "chapter:8/section:57_2/subsection:3",
                "chapter:8/section:57_2/subsection:3/item:1",
                "chapter:8/section:57_2/subsection:3/item:2",
                "chapter:8/section:57_2/subsection:3/item:3",
                "chapter:8/section:57_2/subsection:4",
                "chapter:8/section:57_2/subsection:5",
                "chapter:8/section:57_2/subsection:6",
            )
        ),
    ),
    ("121122010026", "121122010027"): EEPairResidualInventory(
        base_id="121122010026",
        oracle_id="121122010027",
        statute_title="Ringhäälinguseadus",
        comparison_class="commensurable_delta",
        residuals=tuple(
            EEResidualRecord(
                address=address,
                bucket="source_oracle_drift",
                evidence=(
                    "The only in-range amendment between 121122010026 and "
                    "121122010027 is 13310847, and replay applies only that act "
                    "at the oracle cutoff 2011-01-01. Its Ringhäälinguseadus "
                    "block compiles only the euro-conversion rewrites in §§ 43^4 "
                    "and 43^5. None of the current divergences are targeted by "
                    "13310847; oracle 121122010027 instead normalizes untouched "
                    "older text via dash spacing, dash glyphs, quote style, or "
                    "final punctuation at this address."
                ),
            )
            for address in (
                "chapter:1/section:1/subsection:1/item:4",
                "chapter:7_1/section:43_5/subsection:1",
            )
        ),
    ),
    ("116032011011", "104122014014"): EEPairResidualInventory(
        base_id="116032011011",
        oracle_id="104122014014",
        statute_title="Teadus- ja arendustegevuse korralduse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:2/subsection:1/item:8",
                bucket="source_oracle_drift",
                evidence=(
                    "The only in-range amendment between 116032011011 and "
                    "104122014014 is 119062013006. That act targets §§ 9, 12, 13, "
                    "14, 19, 22, 24 and does not touch § 2 subsection 1. "
                    "Oracle 104122014014 carries a revised 'uurimistoetus' "
                    "definition item 8 with different wording from base, but no "
                    "source act introduces that change in the covered window."
                ),
            ),
            EEResidualRecord(
                address="chapter:4/section:14/subsection:7/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Amendment 119062013006 does target § 14, but its replacement "
                    "payload for §14 lg 7 p 2 matches the base text. Oracle "
                    "104122014014 contains an expanded clause 'sealhulgas teadus-ja "
                    "arendustegevuseks vajalike...' not present in the base or in "
                    "119062013006's body. Source act does not introduce this "
                    "addition."
                ),
            ),
            EEResidualRecord(
                address="chapter:4/section:18/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Amendment 119062013006 does not include § 18 in its operation "
                    "list. Oracle 104122014014 carries a revised § 18 lg 1 with "
                    "'mida ei kaeta institutsionaalsest uurimistoetusest' inserted "
                    "after 'infrastruktuurikulud'. No source act in the covered "
                    "window touches § 18 lg 1."
                ),
            ),
            EEResidualRecord(
                address="chapter:4/section:18/subsection:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Same as § 18 lg 1: amendment 119062013006 does not target "
                    "§ 18 lg 2. Oracle carries revised wording with "
                    "'mida ei kaeta institutsionaalsest uurimistoetusest' addition. "
                    "No source act introduces this."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:24",
                bucket="source_oracle_drift",
                evidence=(
                    "Amendment 119062013006 does target § 24 (kehtetuks tunnistamine "
                    "block). Oracle 104122014014 assigns a section title "
                    "'Seaduse kehtetuks tunnistamine' to § 24. The amendment body "
                    "contains the § 24 repeal instruction but without a title node; "
                    "the oracle terviktekst added the title editorially."
                ),
            ),
        ),
    ),
    ("111112022002", "130062023023"): EEPairResidualInventory(
        base_id="111112022002",
        oracle_id="130062023023",
        statute_title="Kalapüügiseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:1/section:8/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130062023001 rewrites § 8(1) with the replay-side text "
                    "about EU Regulation 1224/2009 control system. Oracle 130062023023 "
                    "carries slightly different wording not supported by the source act."
                ),
            ),
            EEResidualRecord(
                address="chapter:7/section:90_2/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130062023001 rewrites § 90^2(1) with replay-side text "
                    "mentioning 'Kliimaministeerium'. Oracle carries 'Keskkonnaministeerium' "
                    "— another ministry rename drift."
                ),
            ),
        ),
    ),
    ("122032024011", "105072025019"): EEPairResidualInventory(
        base_id="122032024011",
        oracle_id="105072025019",
        statute_title="Väärteomenetluse seadustik",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:5/section:31_6/subsection:4",
                bucket="source_pathology",
                evidence=(
                    "None of the five applied amendments (129062024001, 130122024001, "
                    "117042025001, 105072025001, 126062025001) target § 31^6. The XML of "
                    "126062025001 (the largest with 11 ops) contains no mention of section "
                    "31^6. Oracle 105072025019 carries ABIS-related content not backed by "
                    "any source amendment in the visible chain."
                ),
            ),
            EEResidualRecord(
                address="chapter:5/section:31_6/subsection:5",
                bucket="source_pathology",
                evidence=(
                    "Same as subsection 4: no applied amendment targets § 31^6. Oracle "
                    "carries a subsection 5 with an item list about ABIS regulation that "
                    "is not supported by any source amendment."
                ),
            ),
            *(
                EEResidualRecord(
                    address=record.address,
                    bucket=cast(EEResidualBucket, record.bucket),
                    evidence=record.evidence,
                )
                for record in build_inserted_item_omission_family(
                    item_address="chapter:5/section:31_6/subsection:5",
                    source_act_id="105072025001",
                    oracle_id="105072025019",
                    item_labels=("1", "2", "3", "4", "5", "6", "7", "8"),
                )
            ),
            EEResidualRecord(
                address="chapter:5/section:31_6/subsection:5_1",
                bucket="source_pathology",
                evidence=(
                    "Oracle-only subsection 5^1 about ABIS data retention (75 years). "
                    "No applied amendment targets section 31^6."
                ),
            ),
        ),
    ),
    ("106122012015", "104122014005"): EEPairResidualInventory(
        base_id="106122012015",
        oracle_id="104122014005",
        statute_title="Riikliku matusetoetuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="section:13/subsection:3",
                bucket="source_pathology",
                evidence=(
                    "Source act 104122014001 emits a local text_replace for § 13(3), but the "
                    "clause itself says replace 'pensioniamet' with "
                    "'Sotsiaalkindlustusamet'. Base 106122012015 and replay both carry "
                    "'pensionamet' (without the extra 'i'), so the source typo cannot match "
                    "the actual text. Oracle 104122014005 silently normalizes the intended "
                    "rename and therefore differs from the source-backed replay."
                ),
            ),
        ),
    ),
    ("112022020007", "107052025008"): EEPairResidualInventory(
        base_id="112022020007",
        oracle_id="107052025008",
        statute_title="Strateegilise kauba seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/division:2/section:25/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 117042025001 rewrites § 25(3) with the replay-side text "
                    "ending in a terminal period. Oracle 107052025008 preserves the same "
                    "wording but drops the final period, so this is a bounded "
                    "oracle-surface punctuation drift."
                ),
            ),
        ),
    ),
    ("114032025030", "107012025013"): EEPairResidualInventory(
        base_id="114032025030",
        oracle_id="107012025013",
        statute_title="Audiitortegevuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:7/division:1/section:95_2/subsection:1",
                bucket="oracle_correction_notice",
                evidence=(
                    "Source act 107012025001 explicitly extends § 95^2(1) after the word "
                    "„ülevaatust” with the replay-side text "
                    "„või kestlikkusaruande audiitorkontrolli”. LawVM reported the omission "
                    "to Riigi Teataja, and Riigi Teataja confirmed that the omission was as "
                    "described and corrected § 95^2(1), including the translation."
                ),
            ),
        ),
    ),
    ("115032011021", "121122011018"): EEPairResidualInventory(
        base_id="115032011021",
        oracle_id="121122011018",
        statute_title="Saastuse kompleksse vältimise ja kontrollimise seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:2/section:7_2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 121122011001 cleanly inserts the § 7^2 normitehniline märkus "
                    "for Directive 2009/31/EÜ, and replay carries that inserted directive "
                    "note verbatim. Oracle 121122011018 omits the inserted § 7^2 note "
                    "entirely despite the source-backed amendment."
                ),
            ),
        ),
    ),
    ("122122021038", "130062023072"): EEPairResidualInventory(
        base_id="122122021038",
        oracle_id="130062023072",
        statute_title="Soolise võrdõiguslikkuse seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:6",
                bucket="oracle_correction_notice",
                evidence=(
                    "Oracle 130062023072 does not present this as silent source drift: the RT "
                    "XML includes an explicit <veaparandus> notice stating that chapter 6 was "
                    "corrected from 'Sotsiaalministeeriumi ülesanded soolise võrdõiguslikkuse "
                    "seaduse rakendamisel' to 'Majandus- ja Kommunikatsiooniministeeriumi "
                    "ülesanded soolise võrdõiguslikkuse seaduse rakendamisel'. This divergence "
                    "should be treated as an explicit oracle correction lane, not as proof that "
                    "the consolidated text is unsourced."
                ),
            ),
        ),
    ),
    ("122032021008", "131122024048"): EEPairResidualInventory(
        base_id="122032021008",
        oracle_id="131122024048",
        statute_title="Tsiviilseadustiku üldosa seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="part:7/chapter:10/division:5/section:160_1 2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 131122024005 explicitly inserts the normitehniline "
                    "märkus at § 160^1 2) for Directive (EL) 2020/1828, and replay "
                    "preserves that inserted note. Oracle 131122024048 omits the "
                    "note entirely, so this tail is oracle drift rather than a "
                    "replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("116122022028", "130062023098"): EEPairResidualInventory(
        base_id="116122022028",
        oracle_id="130062023098",
        statute_title="Vedelkütuse erimärgistamise seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="section:8_3/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130062023001 emits the generic ministry-reorganization "
                    "rewrite 'Maaeluministeerium' -> 'Regionaal- ja "
                    "Põllumajandusministeerium', and replay preserves that rewrite in "
                    "§ 8^3(1). Oracle 130062023098 still keeps "
                    "'Maaeluministeerium', so this tail is oracle drift rather than a "
                    "replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("116062021002", "127092023009"): EEPairResidualInventory(
        base_id="116062021002",
        oracle_id="127092023009",
        statute_title="Loomakaitseseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:8/section:45/subsection:3",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130062023001 emits the generic ministry-reorganization "
                    "rewrite 'Maaeluministeerium' -> 'Regionaal- ja "
                    "Põllumajandusministeerium', and replay preserves that rewrite in "
                    "§ 45(3). Oracle 127092023009 still keeps "
                    "'Maaeluministeerium', so this tail is oracle drift rather than a "
                    "replay-side amendment bug."
                ),
            ),
        ),
    ),
    ("123032015270", "120122016003"): EEPairResidualInventory(
        base_id="123032015270",
        oracle_id="120122016003",
        statute_title="Rakenduskõrgkooli seadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:5/section:27_3/subsection:3/item:2",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 113122014001 explicitly rewrites § 27^3 lõike 3 "
                    "punkti 2 with the longer 'oma algatusel vabastatud teenistusest "
                    "või töölt päästeasutuses ...' text, and none of the later "
                    "in-range acts 117122015001 or 120122016001 rewrite that item "
                    "again. Replay therefore preserves the source-backed wording, "
                    "while oracle 120122016003 carries a shorter unsourced "
                    "replacement."
                ),
            ),
        ),
    ),
    ("130122025034", "130122025035"): EEPairResidualInventory(
        base_id="130122025034",
        oracle_id="130122025035",
        statute_title="Sotsiaalmaksuseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="section:3/subsection:1/item:18",
                bucket="source_oracle_drift",
                evidence=(
                    "The only visible pair delta between 130122025034 and "
                    "130122025035 comes from source act 114122023001, which only "
                    "repeals § 6 lõike 1 punkti 9 and deletes text from § 7 "
                    "lõikest 3. It does not touch § 3 lõike 1 punkti 18, so replay "
                    "correctly preserves the base terminal semicolon while oracle "
                    "130122025035 silently normalizes that item to a full stop."
                ),
            ),
        ),
    ),
    ("117032011027", "117032011028"): EEPairResidualInventory(
        base_id="117032011027",
        oracle_id="117032011028",
        statute_title="Teeseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:4/section:25_2 7",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 117032011002 cleanly inserts the § 25^2 7 normitehniline "
                    "märkus for Directive 2008/96/EÜ ('seaduse normitehnilist märkust "
                    "täiendatakse tekstiga ...'). Replay preserves that inserted directive "
                    "note, while oracle 117032011028 omits the inserted § 25^2 7 note."
                ),
            ),
        ),
    ),
    ("114032014062", "130012015002"): EEPairResidualInventory(
        base_id="114032014062",
        oracle_id="130012015002",
        statute_title="Kutseseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:5/section:32/subsection:2",
                bucket="source_pathology",
                evidence=(
                    "Base 114032014062 already carries the malformed subsection tail "
                    "'Lisa 1' in § 32(2). The only in-range amendment 130012015001 does not "
                    "touch § 32 at all, so replay correctly preserves that source-side "
                    "appendix leak. Oracle 130012015002 silently drops the stray 'Lisa 1'."
                ),
            ),
        ),
    ),
    ("106082022024", "114032025025"): EEPairResidualInventory(
        base_id="106082022024",
        oracle_id="114032025025",
        statute_title="Kriminaalhooldusseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:8/section:36/subsection:8",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130122024001 is a generic ministry reorganization act and "
                    "emits the statute-wide text replacement 'Justiitsministeerium' -> "
                    "'Justiits- ja Digiministeerium'. Replay preserves that source-backed "
                    "rename in § 36(8), while oracle 114032025025 retains the older "
                    "'Justiitsministeeriumi' wording."
                ),
            ),
        ),
    ),
    ("122122020038", "114032025004"): EEPairResidualInventory(
        base_id="122122020038",
        oracle_id="114032025004",
        statute_title="Advokatuuriseadus",
        comparison_class="commensurable_delta",
        residuals=(
            EEResidualRecord(
                address="chapter:7/section:82_5/subsection:1",
                bucket="source_oracle_drift",
                evidence=(
                    "Source act 130122024001 is a generic ministry reorganization act and "
                    "emits the statute-wide text replacement 'Justiitsministeerium' -> "
                    "'Justiits- ja Digiministeerium'. Replay preserves that source-backed "
                    "rename in § 82^5(1), while oracle 114032025004 retains the older "
                    "'Justiitsministeeriumi' wording."
                ),
            ),
        ),
    ),
    ("123122024006", "103022026015"): EEPairResidualInventory(
        base_id="123122024006",
        oracle_id="103022026015",
        statute_title="Töölepingu seadus",
        comparison_class="commensurable_delta",
        residuals=tuple(
            EEResidualRecord(
                address=record.address,
                bucket=cast(EEResidualBucket, record.bucket),
                evidence=record.evidence,
            )
            for record in build_inserted_note_omission_family(
                note_address="chapter:6/section:115_11",
                note_symbol="§ 115^11",
                source_act_id="103022026005",
                oracle_id="103022026015",
            )
        ),
    ),
    ("118032026018", "118032026019"): EEPairResidualInventory(
        base_id="118032026018",
        oracle_id="118032026019",
        statute_title="Elektrituruseadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(),
    ),
    ("118032026031", "118032026032"): EEPairResidualInventory(
        base_id="118032026031",
        oracle_id="118032026032",
        statute_title="Energiamajanduse korralduse seadus",
        comparison_class="same_chain_editorial_drift",
        residuals=(),
    ),
    ("123122022037", "104122024010"): EEPairResidualInventory(
        base_id="123122022037",
        oracle_id="104122024010",
        statute_title="Kinnisasja avalikes huvides omandamise seadus",
        comparison_class="commensurable_delta",
        residuals=(),
    ),
}

_SOLVED_EE_RESIDUALS: set[tuple[str, str]] = {
    ("109042021007", "114032025016"),
    ("106122012015", "104122014005"),
    ("114032014062", "130012015002"),
}


def _generated_ee_residual_records(base_id: str, oracle_id: str) -> tuple[EEResidualRecord, ...]:
    """Return generated evidence records for the pairs covered by helper families."""
    match (base_id, oracle_id):
        case ("128122018041", "104122019022"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_oracle_drift",
                    records=(
                        (
                            "chapter:2/division:6",
                            "Base 128122018041 already carries repealed division 6 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Loomsete jäätmete käitlemine' anyway.",
                        ),
                        (
                            "chapter:2/division:7",
                            "Base 128122018041 already carries repealed division 7 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Loomade ja loomsete saaduste "
                            "sisse- ja väljavedu' anyway.",
                        ),
                        (
                            "chapter:2/division:8",
                            "Base 128122018041 already carries repealed division 8 with an "
                            "empty `jaguPealkiri`, and the in-range amendments 113032019002 / "
                            "104122019002 do not retitle or reinsert that division heading. "
                            "Oracle 104122019022 fills in 'Veterinaartõendid' anyway.",
                        ),
                    ),
                ),
            )
        case ("115032014084", "101092015036"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    records=(
                        (
                            "section:23/subsection:1",
                            "Source act 130062015004 is a generic ministry reorganization "
                            "rename and emits the statute-wide text replacement "
                            "'Põllumajandusministeerium' -> 'Maaeluministeerium'. Replay preserves "
                            "that source-backed rename in § 23(1), while oracle 101092015036 "
                            "retains the older ministry name.",
                        ),
                        (
                            "section:23/subsection:4",
                            "Source act 130062015004 emits the same generic "
                            "'Põllumajandusministeerium' -> 'Maaeluministeerium' rename for the "
                            "whole statute. Replay updates § 23(4) accordingly, while oracle "
                            "101092015036 keeps 'Põllumajandusministeerium'.",
                        ),
                    ),
                ),
            )
        case ("111112022002", "130062023023"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    records=(
                        (
                            "chapter:7/section:90_2/subsection:1",
                            "Source act 130062023001 rewrites § 90^2(1) with replay-side text "
                            "mentioning 'Kliimaministeerium'. Oracle carries 'Keskkonnaministeerium' "
                            "— another ministry rename drift."
                        ),
                    ),
                ),
            )
        case ("109042021007", "114032025016"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_oracle_drift",
                    records=(
                        (
                            "chapter:2/division:2/section:13/subsection:1",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                        (
                            "chapter:2/division:2/section:13/subsection:2",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment 'täitemenetlusregister' wording; "
                            "oracle 114032025016 carries the 109042021001 'täitmisregister' text.",
                        ),
                    ),
                ),
                build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:2/division:5/section:37_2/subsection:1",
                            "Source act 109042021001 emits § 37^2(1) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:2",
                            "Source act 109042021001 emits § 37^2(2) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3",
                            "Source act 109042021001 emits § 37^2(3) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3/item:1",
                            "Source act 109042021001 emits § 37^2(3) p 1 cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:3/item:2",
                            "Source act 109042021001 emits § 37^2(3) p 2 cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                        (
                            "chapter:2/division:5/section:37_2/subsection:4",
                            "Source act 109042021001 emits § 37^2(4) cleanly, but base "
                            "109042021007 already cites that act while omitting it. Oracle "
                            "114032025016 contains it.",
                        ),
                    ),
                ),
                build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:2/division:5/section:40_1/subsection:1",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment subsection text for § 40^1(1); "
                            "oracle 114032025016 carries the 109042021001 wording.",
                        ),
                        (
                            "chapter:2/division:5/section:40_1/subsection:2",
                            "Base 109042021007 cites amendment 109042021001 in its own source refs "
                            "but still preserves the pre-amendment subsection text for § 40^1(2); "
                            "oracle 114032025016 carries the 109042021001 wording.",
                        ),
                    ),
                ),
            )
        case ("106052020036", "103022026013"):
            return _lower_generated_residual_records(
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
                ),
            )
        case ("121052014030", "121052014031"):
            return ()
        case ("123122022037", "104122024010"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_oracle_drift",
                    records=(
                        (
                            "chapter:3/section:12/subsection:5",
                            "Source act 106072023002 rewrites § 12(5) with a terminal period, "
                            "and replay preserves that source-side final punctuation. Oracle "
                            "104122024010 drops the closing period instead. This is a bounded "
                            "oracle-surface punctuation drift, not a replay omission.",
                        ),
                    ),
                ),
            )
        case ("121122010026", "121122010027"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_oracle_drift",
                    records=(
                        (
                            "chapter:1/section:1/subsection:1/item:4",
                            "The only in-range amendment between 121122010026 and "
                            "121122010027 is 13310847, and replay applies only that act "
                            "at the oracle cutoff 2011-01-01. Its Ringhäälinguseadus "
                            "block compiles only the euro-conversion rewrites in §§ 43^4 "
                            "and 43^5. None of the current divergences are targeted by "
                            "13310847; oracle 121122010027 instead normalizes untouched "
                            "older text via dash spacing, dash glyphs, quote style, or "
                            "final punctuation at this address."
                        ),
                        (
                            "chapter:7_1/section:43_5/subsection:1",
                            "The only in-range amendment between 121122010026 and "
                            "121122010027 is 13310847, and replay applies only that act "
                            "at the oracle cutoff 2011-01-01. Its Ringhäälinguseadus "
                            "block compiles only the euro-conversion rewrites in §§ 43^4 "
                            "and 43^5. None of the current divergences are targeted by "
                            "13310847; oracle 121122010027 instead normalizes untouched "
                            "older text via dash spacing, dash glyphs, quote style, or "
                            "final punctuation at this address."
                        ),
                    ),
                ),
            )
        case ("108072025061", "107012026021"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:11/section:68_5/subsection:4",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:11/section:68_5/subsection:5",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_2/subsection:2",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_2/subsection:2_1",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_5",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_5/subsection:1",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_5/subsection:2",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:3_1/section:21_5/subsection:3",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:5/section:32/subsection:10",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:5/section:32/subsection:8",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:5/section:32/subsection:9",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:1",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:10",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:2",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:2_1",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:2_2",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                        (
                            "chapter:8/section:55_3/subsection:3",
                            "The sole new amendment reference between 108072025061 and "
                            "107012026021 is 107012026004, and replay applies only that act "
                            "at the oracle cutoff 2026-07-01. Its Keskkonnatasude seaduse "
                            "§ 4 block compiles to 20 ops covering the early 2026 "
                            "energiakasutus changes (§§ 3, 4, 5, 14, 18^1, 20^1, 21, 22, "
                            "25^1, 26, 32(6/6^1/6^5), 33^1(6), 61(1)) and does not mention "
                            "§§ 21^2, 21^5, 32(8–10), 55^3, or 68^5. Oracle 107012026021 "
                            "nonetheless introduces the replay-diverging tuuleenergiast "
                            "elektrienergia tootmise tasu cluster at this address."
                        ),
                    ),
                ),
            )
        case ("104122024013", "128012026005"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
                    bucket="source_pathology",
                    records=(
                        (
                            "chapter:8/section:57/subsection:6",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57/subsection:7",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_1",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_1/subsection:1",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_1/subsection:1/item:1",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_1/subsection:1/item:2",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_1/subsection:1/item:3",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:1",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:2",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:3",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:3/item:1",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:3/item:2",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:3/item:3",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:4",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:5",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                        (
                            "chapter:8/section:57_2/subsection:6",
                            "The only in-range amendments between 104122024013 and "
                            "128012026005 are 112072025001 and 128012026001. Replay "
                            "applies exactly those acts at the oracle cutoff 2026-02-07, "
                            "and they compile only the § 68^3 p 1 repeal plus the new "
                            "§ 30(3^2) and § 31(2^1) inserts. Neither act mentions "
                            "§ 57(6–7), § 57^1, or § 57^2, but oracle 128012026005 "
                            "nonetheless blanks or omits that entire mingi/kähriku farm "
                            "cluster at this address."
                        ),
                    ),
                ),
            )
        case ("126042013006", "111042014003"):
            return _lower_generated_residual_records(
                build_shortened_section_family(
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
                    ),
                ),
                build_shortened_section_family(
                    records=(
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
                ),
            )
        case ("122032024011", "105072025019"):
            return _lower_generated_residual_records(
                build_inserted_item_omission_family(
                    item_address="chapter:5/section:31_6/subsection:5",
                    source_act_id="105072025001",
                    oracle_id="105072025019",
                    item_labels=("1", "2", "3", "4", "5", "6", "7", "8"),
                ),
            )
        case ("123122024006", "103022026015"):
            return _lower_generated_residual_records(
                build_inserted_note_omission_family(
                    note_address="chapter:6/section:115_11",
                    note_symbol="§ 115^11",
                    source_act_id="103022026005",
                    oracle_id="103022026015",
                ),
            )
        case _:
            return ()


def _compose_generated_first_inventory(
    inventory: EEPairResidualInventory,
) -> EEPairResidualInventory:
    """Return an inventory where generated evidence wins before manual fallback."""
    generated = _generated_ee_residual_records(inventory.base_id, inventory.oracle_id)
    if not generated:
        return inventory

    generated_by_address = {record.address: record for record in generated}
    manual_addresses = {record.address for record in inventory.residuals}
    merged_residuals = []
    seen_generated_addresses: set[str] = set()

    for record in inventory.residuals:
        generated_record = generated_by_address.get(record.address)
        if generated_record is None:
            merged_residuals.append(record)
            continue
        merged_residuals.append(generated_record)
        seen_generated_addresses.add(record.address)

    for record in generated:
        if record.address not in seen_generated_addresses and record.address not in manual_addresses:
            merged_residuals.append(record)

    residuals = tuple(merged_residuals)
    if residuals == inventory.residuals:
        return inventory
    return EEPairResidualInventory(
        base_id=inventory.base_id,
        oracle_id=inventory.oracle_id,
        statute_title=inventory.statute_title,
        comparison_class=inventory.comparison_class,
        residuals=residuals,
    )


def get_ee_residual_inventory(base_id: str, oracle_id: str) -> EEPairResidualInventory | None:
    """Return the known residual inventory for one EE base/oracle pair."""
    if (base_id, oracle_id) in _SOLVED_EE_RESIDUALS:
        return None
    inventory = _KNOWN_EE_RESIDUALS.get((base_id, oracle_id))
    if inventory is None:
        return None
    return _compose_generated_first_inventory(inventory)


def list_known_ee_residual_inventories() -> tuple[EEPairResidualInventory, ...]:
    """Return all known EE residual inventories."""
    return tuple(
        _compose_generated_first_inventory(inventory)
        for key, inventory in _KNOWN_EE_RESIDUALS.items()
        if key not in _SOLVED_EE_RESIDUALS
    )


__all__ = [
    "EEPairResidualInventory",
    "EEResidualRecord",
    "EEResidualBucket",
    "get_ee_residual_inventory",
    "list_known_ee_residual_inventories",
]
