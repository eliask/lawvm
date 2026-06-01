from __future__ import annotations

from lawvm.uk_legislation.nlp_parser import US, parse_fragment_substitution
from lawvm.uk_legislation.source_text_normalization import normalize_uk_parser_text


def test_normalize_uk_parser_text_preserves_quoted_dash_payloads() -> None:
    text = 'at the end insert\u2013 "A\u2013B"'

    assert normalize_uk_parser_text(text) == 'at the end insert\u2014 "A\u2013B"'


def test_normalize_uk_parser_text_does_not_treat_word_apostrophe_as_quote() -> None:
    text = "Minister's functions\u2013 at the end insert - X"

    assert normalize_uk_parser_text(text) == "Minister's functions\u2014 at the end insert - X"


def test_normalize_uk_parser_text_keeps_single_quoted_payload_dash_and_apostrophe() -> None:
    text = "at the end insert\u2013 'tenant's A\u2013B'"

    assert normalize_uk_parser_text(text) == "at the end insert\u2014 'tenant's A\u2013B'"


def test_normalize_uk_parser_text_repairs_instruction_token_joins_outside_quotes() -> None:
    text = (
        'for the words "X" onwards thereshall be substituted '
        '"personcharged"; the words from "Y" onwards shall beomitted'
    )

    assert normalize_uk_parser_text(text) == (
        'for the words "X" onwards there shall be substituted '
        '"personcharged"; the words from "Y" onwards shall be omitted'
    )


def test_parse_fragment_substitution_accepts_dash_variants_outside_quotes() -> None:
    subs = parse_fragment_substitution("at the end insert\u2013 and section 15 .")

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "and section 15",
            "rule_id": "uk_effect_at_end_unquoted_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_sentence_bounded_at_end_insert() -> None:
    subs = parse_fragment_substitution(
        "3 At the end of subsection (3) insert before the end of the period of "
        "2 years beginning with the date on which the conviction of the person "
        "concerned is reversed or he is pardoned. 3A But the Secretary of State "
        "may direct that an application is late."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": (
                "before the end of the period of 2 years beginning with the date "
                "on which the conviction of the person concerned is reversed or he is pardoned"
            ),
            "rule_id": "uk_effect_at_end_unquoted_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_returns_fresh_fragment_dicts() -> None:
    text = 'for "old words" substitute "new words"'
    first = parse_fragment_substitution(text)
    first[0]["caller_added_context"] = "mutated"

    second = parse_fragment_substitution(text)

    assert second == [{"original": "old words", "replacement": "new words"}]


def test_parse_fragment_substitution_handles_there_is_inserted() -> None:
    subs = parse_fragment_substitution(
        'c in subsection (6) after “Agency,” there is inserted '
        '“by the Director General of the Scottish Crime and Drug Enforcement Agency,”.'
    )

    assert subs == [
        {
            "original": "Agency,",
            "replacement": (
                "Agency, by the Director General of the Scottish Crime and Drug "
                "Enforcement Agency,"
            ),
            "rule_id": "uk_effect_after_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_the_word_there_is_inserted() -> None:
    subs = parse_fragment_substitution(
        "a after the word \u201cpossession\u201d there is inserted \u201c or an eviction order \u201d."
    )

    assert subs == [
        {
            "original": "possession",
            "replacement": "possession or an eviction order ",
            "rule_id": "uk_effect_after_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_secondly_occurring_insert() -> None:
    subs = parse_fragment_substitution(
        'in sub-paragraph (iv), after “board”, where secondly occurring, '
        'there is inserted “ , a Transport Partnership ” .'
    )

    assert subs == [
        {
            "original": "board",
            "replacement": "board , a Transport Partnership ",
            "occurrence": "2",
            "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_joined_after_insert_token() -> None:
    subs = parse_fragment_substitution(
        "in the definition of “the 2001 Directive”, after “human use”insert “, as amended”."
    )

    assert subs == [
        {
            "original": "human use",
            "replacement": "human use, as amended",
            "rule_id": "uk_effect_after_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_parenthetical_each_occurrence_substitution() -> None:
    subs = parse_fragment_substitution(
        "In subsection (5), for “Commission” (at each place where it occurs) "
        "substitute “appropriate committee”."
    )

    assert subs == [
        {
            "original": "Commission",
            "replacement": "appropriate committee",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_entry_defining_repeal() -> None:
    subs = parse_fragment_substitution(
        "omit the entry defining “clinical trial” and “clinical trial certificate”, and"
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_clinical trial",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
        {
            "original": "TEXT_DEFINITION_ENTRY_clinical trial certificate",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_where_it_appears_for_ordinal_block_insert() -> None:
    subs = parse_fragment_substitution(
        "after “Northern Ireland”, where it appears for the second time, insert— "
        "or the register of visiting pharmaceutical chemists from a relevant European State ."
    )

    assert subs == [
        {
            "original": "Northern Ireland",
            "replacement": (
                "Northern Ireland or the register of visiting pharmaceutical chemists "
                "from a relevant European State"
            ),
            "occurrence": "2",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_block_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_every_reference_substitution() -> None:
    subs = parse_fragment_substitution(
        "for every reference to “the Minister of Health and Social Services for Northern Ireland” "
        "substitute “the Minister for Health, Social Services and Public Safety”;"
    )

    assert subs == [
        {
            "original": "the Minister of Health and Social Services for Northern Ireland",
            "replacement": "the Minister for Health, Social Services and Public Safety",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_first_occurrence_range_to_end() -> None:
    subs = parse_fragment_substitution(
        "for the words from the first occurrence of “for the purposes” to the end "
        "of the subsection substitute “as prescription only medicines”;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_for the purposes_TO_END",
            "replacement": "as prescription only medicines",
            "occurrence": "1",
        }
    ]


def test_parse_fragment_substitution_handles_semicolon_terminated_at_end_block_insert() -> None:
    subs = parse_fragment_substitution(
        "at the end insert— or a person registered in the register of visiting "
        "pharmaceutical chemists from a relevant European State maintained under "
        "Article 9 of the Pharmacy (Northern Ireland) Order 1976 . ;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": (
                "or a person registered in the register of visiting pharmaceutical "
                "chemists from a relevant European State maintained under Article 9 "
                "of the Pharmacy (Northern Ireland) Order 1976"
            ),
            "rule_id": "uk_effect_at_end_unquoted_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_bare_quoted_anchor_insert() -> None:
    subs = parse_fragment_substitution(
        "i \u201c18,\u201d there shall be inserted \u201c 18A, 18B, 18C, \u201d ; and"
    )

    assert subs == [
        {
            "original": "18,",
            "replacement": "18, 18A, 18B, 18C, ",
            "rule_id": "uk_effect_bare_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_bare_word_quoted_anchor_insert() -> None:
    subs = parse_fragment_substitution(
        "ii the word \u201c28\u201d there shall be inserted \u201c , 28A \u201d."
    )

    assert subs == [
        {
            "original": "28",
            "replacement": "28 , 28A ",
            "rule_id": "uk_effect_bare_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_bare_anchor_insert_with_trailing_comma() -> None:
    subs = parse_fragment_substitution(
        "iv \u201cAct\u201d there shall be inserted \u201c and to sections 52 to 56 \u201d,"
    )

    assert subs == [
        {
            "original": "Act",
            "replacement": "Act and to sections 52 to 56 ",
            "rule_id": "uk_effect_bare_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_in_both_places_insert() -> None:
    subs = parse_fragment_substitution(
        "b in that sub-paragraph, after \u201cmember\u2019s entitlement to\u201d, "
        "in both places insert \u201c, or to the payment of,\u201d ."
    )

    assert subs == [
        {
            "original": "member\u2019s entitlement to",
            "replacement": "member\u2019s entitlement to, or to the payment of,",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_in_each_place_insert() -> None:
    subs = parse_fragment_substitution(
        "9 In paragraph 10, after \u201cday\u201d, in each place it occurs, "
        "insert \u201cunder any retained direct EU legislation\u201d."
    )

    assert subs == [
        {
            "original": "day",
            "replacement": "day under any retained direct EU legislation",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_in_each_place_occurring_insert() -> None:
    subs = parse_fragment_substitution(
        "3 In subsection (6), after \u201cBoard\u201d, in each place occurring, "
        "insert \u201cor Canal & River Trust\u201d."
    )

    assert subs == [
        {
            "original": "Board",
            "replacement": "Board or Canal & River Trust",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_in_both_places_where_it_appears_insert() -> None:
    subs = parse_fragment_substitution(
        "b after \u201ccourt\u201d, in both places where it appears, "
        "insert \u201cor the First-tier Tribunal\u201d."
    )

    assert subs == [
        {
            "original": "court",
            "replacement": "court or the First-tier Tribunal",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_words_in_each_place_where_they_occur_insert() -> None:
    subs = parse_fragment_substitution(
        "3 In subsections (2), (6) and (7)(b)(i), after the words “the OFT ”, "
        "in each place where they occur, there shall be inserted “ and OFCOM ”."
    )

    assert subs == [
        {
            "original": "the OFT ",
            "replacement": "the OFT  and OFCOM ",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_on_each_occasion_insert() -> None:
    subs = parse_fragment_substitution(
        "6 In section 218, after \u201ccourt\u201d, on each occasion where it appears, "
        "insert \u201cor the First-tier Tribunal\u201d."
    )

    assert subs == [
        {
            "original": "court",
            "replacement": "court or the First-tier Tribunal",
            "rule_id": "uk_effect_after_quoted_anchor_each_occasion_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_both_places_parenthesis_insert() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (2) after \u201cwithdrawal agreement\u201d, "
        "in both places those words occur in parenthesis, "
        "insert \u201c(including the Windsor Framework)\u201d ;"
    )

    assert subs == [
        {
            "original": "withdrawal agreement",
            "replacement": "withdrawal agreement (including the Windsor Framework)",
            "rule_id": "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_all_occurrences_substitution() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (4), for \u201cthe commencement date\u201d, "
        "in each place it occurs, substitute \u201c1 December 2020\u201d;"
    )

    assert subs == [
        {
            "original": "the commencement date",
            "replacement": "1 December 2020",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_all_occurrences_alternative_preimages() -> None:
    subs = parse_fragment_substitution(
        "In each of the enactments listed in sub-paragraph (3) for "
        "\u201cSupreme Court\u201d or \u201cSupreme Court of Judicature\u201d "
        "in each place substitute \u201cSenior Courts\u201d."
    )

    assert subs == [
        {
            "original": "Supreme Court",
            "replacement": "Senior Courts",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        },
        {
            "original": "Supreme Court of Judicature",
            "replacement": "Senior Courts",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_each_case_occurs_substitution() -> None:
    subs = parse_fragment_substitution(
        "a for \u201can exit charge payment plan\u201d, in each case it occurs, "
        "substitute \u201c a CT exit charge payment plan \u201d ,"
    )

    assert subs == [
        {
            "original": "an exit charge payment plan",
            "replacement": " a CT exit charge payment plan ",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_each_place_occurring_substitution() -> None:
    subs = parse_fragment_substitution(
        "b for \u201cthe Board\u201d, in each place occurring, substitute \u201cCanal & River Trust\u201d."
    )

    assert subs == [
        {
            "original": "the Board",
            "replacement": "Canal & River Trust",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_preposed_beginning_insert() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (4)(a) there shall be inserted at the beginning the "
        "words “subject to subsection (4B) below,”;"
    )

    assert subs == [
        {
            "original": "TEXT_BEGINNING",
            "replacement": "subject to subsection (4B) below,",
            "rule_id": "uk_effect_preposed_beginning_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_parenthesized_anchor_insert() -> None:
    subs = parse_fragment_substitution("b after (3) insert “or (3ZA)” .")

    assert subs == [
        {
            "original": "(3)",
            "replacement": "(3) or (3ZA)",
            "rule_id": "uk_effect_after_parenthesized_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_does_not_treat_explicit_child_insert_as_parenthesized_text_anchor() -> None:
    subs = parse_fragment_substitution("after subsection (3) insert “or (3ZA)”")

    assert subs == [
        {
            "original": "TEXT_AFTER_CHILD_subsection_3",
            "replacement": "or (3ZA)",
            "rule_id": "uk_effect_after_child_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_quoted_word_shall_be_omitted() -> None:
    subs = parse_fragment_substitution('i after paragraph (a) the word “or” shall be omitted; and')

    assert subs == [
        {
            "original": "or",
            "replacement": "",
            "rule_id": "uk_effect_quoted_word_passive_omit_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_post_child_quoted_word_repealed() -> None:
    subs = parse_fragment_substitution('i the “and” after paragraph (b) is repealed,')

    assert subs == [
        {
            "original": "and",
            "replacement": "",
            "rule_id": "uk_effect_quoted_word_passive_omit_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_respectively_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        "1 In each of the following provisions of the 2002 Act, for the words "
        "“Commissioner” and “Commissioner's” wherever occurring there is substituted "
        "“ Commission ” and “ Commission's ” respectively—"
    )

    assert subs == [
        {
            "original": "Commissioner",
            "replacement": "Commission",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "Commissioner's",
            "replacement": "Commission's",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_respectively_in_each_place_series() -> None:
    subs = parse_fragment_substitution(
        "h in paragraph 7, for “Director” (in each place), “he” (in each place) "
        "and “him” there is substituted “OFT”, “it” and “it” respectively, and, "
        "in the cross-heading before that paragraph, for “ Director ” there is "
        "substituted “ OFT ”;"
    )

    assert subs == [
        {
            "original": "Director",
            "replacement": "OFT",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "he",
            "replacement": "it",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "him",
            "replacement": "it",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": " Director ",
            "replacement": " OFT ",
        },
    ]


def test_parse_fragment_substitution_handles_respectively_four_term_series() -> None:
    subs = parse_fragment_substitution(
        "ii for “Director” (in each place), “he” (in each place), “his” "
        "(in each place) and “Director's” there is substituted “OFT”, “it”, "
        "“its” and “OFT's” respectively;"
    )

    assert subs == [
        {
            "original": "Director",
            "replacement": "OFT",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "he",
            "replacement": "it",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "his",
            "replacement": "its",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
        {
            "original": "Director's",
            "replacement": "OFT's",
            "rule_id": "uk_effect_respectively_all_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_keeps_unqualified_respectively_series_blocked() -> None:
    subs = parse_fragment_substitution(
        "for “Director” and “Secretary” there is substituted “OFT” and “Authority” respectively"
    )

    assert subs == [
        {
            "original": "Director” and “Secretary",
            "replacement": "OFT",
        }
    ]


def test_parse_fragment_substitution_handles_nested_quote_substitution() -> None:
    subs = parse_fragment_substitution(
        "iii for the words \u201ca medical practitioner (the \u201cnominated medical "
        "practitioner\u201d)\u201d substitute \u201ca practitioner (the \u201cnominated "
        "practitioner\u201d)\u201d ;"
    )

    assert subs[0] == {
        "original": "a medical practitioner (the \u201cnominated medical practitioner\u201d)",
        "replacement": "a practitioner (the \u201cnominated practitioner\u201d)",
        "rule_id": "uk_effect_nested_quote_substitution_text_patch",
    }


def test_parse_fragment_substitution_handles_quoted_anchor_block_substitution() -> None:
    subs = parse_fragment_substitution(
        "22 In Part 3, for \u201cAn officer of the department of the Secretary of State "
        "for Business, Energy and Industrial Strategy\u201d substitute\u2014 "
        "An officer of the department of the Secretary of State for Business and Trade."
    )

    assert subs == [
        {
            "original": (
                "An officer of the department of the Secretary of State for "
                "Business, Energy and Industrial Strategy"
            ),
            "replacement": (
                "An officer of the department of the Secretary of State for "
                "Business and Trade."
            ),
            "rule_id": "uk_effect_quoted_anchor_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_where_ordinal_occurs_substitution() -> None:
    subs = parse_fragment_substitution(
        "ii for the words \u201cmedical practitioner\u201d, where they second occur, "
        "substitute \u201cperson who issued the certificate\u201d ;"
    )

    assert subs == [
        {
            "original": "medical practitioner",
            "replacement": "person who issued the certificate",
            "occurrence": "2",
            "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_occurs_for_ordinal_time_substitution() -> None:
    subs = parse_fragment_substitution(
        "iii for \u201cthe\u201d, where it occurs for the second time, substitute \u201c a \u201d ,"
    )

    assert subs == [
        {
            "original": "the",
            "replacement": "a",
            "occurrence": "2",
            "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_where_multiple_ordinal_occurs_substitution() -> None:
    subs = parse_fragment_substitution(
        "a for the word \u201cinterim\u201d, where it first and third occurs, "
        "substitute \u201cthe\u201d;"
    )

    assert subs == [
        {
            "original": "interim",
            "replacement": "the",
            "occurrence": "3",
            "rule_id": "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch",
        },
        {
            "original": "interim",
            "replacement": "the",
            "occurrence": "1",
            "rule_id": "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_multiple_ordinal_places_substitution() -> None:
    subs = parse_fragment_substitution(
        "ii for \u201cit\u201d, in the first and third places where it occurs, "
        "substitute \u201che\u201d;"
    )

    assert subs == [
        {
            "original": "it",
            "replacement": "he",
            "occurrence": "3",
            "rule_id": "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch",
        },
        {
            "original": "it",
            "replacement": "he",
            "occurrence": "1",
            "rule_id": "uk_effect_quoted_word_where_ordinal_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_both_subsequent_places_substitution() -> None:
    subs = parse_fragment_substitution(
        "b for \u201cappeal\u201d, where it appears in both subsequent places, "
        "substitute \u201creview or appeal\u201d."
    )

    assert subs == [
        {
            "original": "appeal",
            "replacement": "review or appeal",
            "occurrence": "3",
            "rule_id": "uk_effect_both_subsequent_occurrences_substitution_text_patch",
        },
        {
            "original": "appeal",
            "replacement": "review or appeal",
            "occurrence": "2",
            "rule_id": "uk_effect_both_subsequent_occurrences_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_where_bare_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "b for \u201cthe adult\u201d, where first occurring, "
        "substitute \u201c an adult with incapacity \u201d ,"
    )

    assert subs == [
        {
            "original": "the adult",
            "replacement": "an adult with incapacity",
            "occurrence": "1",
            "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_where_occurs_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "c in sub-paragraph (3), for \u201cthe earlier year\u201d, where it occurs first, "
        "substitute \u201c an earlier year \u201d ;"
    )

    assert subs == [
        {
            "original": "the earlier year",
            "replacement": "an earlier year",
            "occurrence": "1",
            "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_parenthesized_where_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1), for \u201cofficers\u201d (where it first occurs) "
        "substitute \u201c persons \u201d ;"
    )

    assert subs == [
        {
            "original": "officers",
            "replacement": "persons",
            "occurrence": "1",
            "rule_id": "uk_effect_post_quoted_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_ordinal_place_substitution_with_words_wrapper() -> None:
    subs = parse_fragment_substitution(
        "b for the words \u201cthe trustee\u201d, in the first place where they occur, "
        "there shall be substituted the words \u201c any relevant trustee \u201d ."
    )

    assert subs == [
        {
            "original": "the trustee",
            "replacement": "any relevant trustee",
            "occurrence": "1",
            "rule_id": "uk_effect_post_quoted_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_wherever_occurring_substitution() -> None:
    subs = parse_fragment_substitution(
        "b in subsections (2), (3) and (6), for \u201cthe Information Centre\u201d, "
        "wherever occurring, substitute \u201cNHS England\u201d ."
    )

    assert subs == [
        {
            "original": "the Information Centre",
            "replacement": "NHS England",
            "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_wherever_occurring_passive_substitution() -> None:
    subs = parse_fragment_substitution(
        "24 In section 52, for \u201cthe Commission\u201d, wherever occurring, "
        "there shall be substituted \u201c OFCOM \u201d ."
    )

    assert subs == [
        {
            "original": "the Commission",
            "replacement": "OFCOM",
            "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_multiple_wherever_occurring_passive_substitution() -> None:
    subs = parse_fragment_substitution(
        "6 In section 14, for \u201cThe Commission\u201d and \u201cthe Commission\u201d, wherever "
        "occurring, there shall be substituted \u201c OFCOM \u201d ."
    )

    assert subs == [
        {
            "original": "The Commission",
            "replacement": "OFCOM",
            "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
        },
        {
            "original": "the Commission",
            "replacement": "OFCOM",
            "rule_id": "uk_effect_wherever_occurring_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_parenthesized_all_occurrences_substitution() -> None:
    subs = parse_fragment_substitution(
        "e in subsection (7), for \u201c retained EU \u201d "
        "(in each place it appears) substitute \u201cassimilated\u201d ;"
    )

    assert subs == [
        {
            "original": " retained EU ",
            "replacement": "assimilated",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_words_in_brackets_substitution() -> None:
    subs = parse_fragment_substitution(
        "9 In section 100(6), for the words in brackets substitute "
        "\u201c(including a case where the amount is \u00a30)\u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_IN_BRACKETS",
            "replacement": "(including a case where the amount is \u00a30)",
            "rule_id": "uk_effect_words_in_brackets_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_first_and_second_occurrence_substitution() -> None:
    subs = parse_fragment_substitution(
        "i for \u201c retained \u201d (in the first and second places it appears) "
        "substitute \u201cassimilated\u201d ;"
    )

    assert subs == [
        {
            "original": " retained ",
            "replacement": "assimilated",
            "occurrence": "2",
            "rule_id": "uk_effect_first_second_occurrence_substitution_text_patch",
        },
        {
            "original": " retained ",
            "replacement": "assimilated",
            "occurrence": "1",
            "rule_id": "uk_effect_first_second_occurrence_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_first_two_places_substitution() -> None:
    subs = parse_fragment_substitution(
        "i for \u201c, a Health Board or the Agency\u201d, in the first two places "
        "where it occurs, substitute \u201c or a Health Board \u201d , and"
    )

    assert subs == [
        {
            "original": ", a Health Board or the Agency",
            "replacement": " or a Health Board ",
            "occurrence": "2",
            "rule_id": "uk_effect_first_second_occurrence_substitution_text_patch",
        },
        {
            "original": ", a Health Board or the Agency",
            "replacement": " or a Health Board ",
            "occurrence": "1",
            "rule_id": "uk_effect_first_second_occurrence_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "8 In Schedule 16, in paragraph 11(4)(b), for first \u201cby\u201d substitute \u201cbe\u201d ."
    )

    assert subs == [
        {
            "original": "by",
            "replacement": "be",
            "occurrence": "1",
            "rule_id": "uk_effect_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_the_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "a for the first \u201cthe closure notice\u201d substitute "
        "\u201c a partial or final closure notice \u201d ;"
    )

    assert subs == [
        {
            "original": "the closure notice",
            "replacement": " a partial or final closure notice ",
            "occurrence": "1",
            "rule_id": "uk_effect_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_post_quoted_ordinal_substitution() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph (b) for \u201csix months\u201d in the first place it occurs substitute \u201cfour months\u201d,"
    )

    assert subs == [
        {
            "original": "six months",
            "replacement": "four months",
            "occurrence": "1",
            "rule_id": "uk_effect_post_quoted_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_post_quoted_ordinal_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        "ii for the word “order” in the second place where it appears there is substituted “ scheme ” ."
    )

    assert subs == [
        {
            "original": "order",
            "replacement": "scheme",
            "occurrence": "2",
            "rule_id": "uk_effect_post_quoted_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_parenthesized_nested_quote_substitution() -> None:
    subs = parse_fragment_substitution(
        "5 In section 293(2)(d), for \u201c(\u201ca progress report\u201d) "
        "substitute \u201c(a \u201cprogress report\u201d)\u201d."
    )

    assert subs == [
        {
            "original": "(\u201ca progress report\u201d)",
            "replacement": "(a \u201cprogress report\u201d)",
            "rule_id": "uk_effect_parenthesized_nested_quote_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_relating_repeal() -> None:
    subs = parse_fragment_substitution(
        "a omit the definition relating to the Service Complaints Commissioner, and"
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_the Service Complaints Commissioner",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_anchor_to_end_block_substitution() -> None:
    subs = parse_fragment_substitution(
        "i in sub-paragraph (1)(a), from \u201coffence under\u201d to the end substitute\u2014 "
        "offence under\u2014 i section 28, ii regulation 60A of the Medical Devices "
        "Regulations 2002, or iii regulation 23 of the Medical Devices "
        "(Northern Ireland Protocol) Regulations 2021, ;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_offence under_TO_END",
            "replacement": (
                "offence under\u2014 i section 28, ii regulation 60A of the Medical Devices "
                "Regulations 2002, or iii regulation 23 of the Medical Devices "
                "(Northern Ireland Protocol) Regulations 2021,"
            ),
            "rule_id": "uk_effect_anchor_to_end_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_anchor_onwards_block_substitution() -> None:
    subs = parse_fragment_substitution(
        "4 In subsection (4), for the words from \u201cwhether as being\u201d onwards "
        "substitute if he is\u2014 a a person against whom proceedings are taken."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_whether as being_TO_END",
            "replacement": "if he is\u2014 a a person against whom proceedings are taken.",
            "rule_id": "uk_effect_anchor_to_end_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_ordinal_insert() -> None:
    subs = parse_fragment_substitution(
        "i after \u201cSecretary of State\u201d, in the first place it occurs, "
        "insert \u201cand the Northern Ireland Department\u201d ;"
    )

    assert subs == [
        {
            "original": "Secretary of State",
            "replacement": "Secretary of State and the Northern Ireland Department",
            "occurrence": "1",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_ordinal_places_where_insert() -> None:
    subs = parse_fragment_substitution(
        "3 In section 56C(1), after \u201cthe\u201d, in the first and second places "
        "where it occurs, insert \u201cAccountant in Bankruptcy, or as the case may be, the\u201d."
    )

    assert subs == [
        {
            "original": "the",
            "replacement": "the Accountant in Bankruptcy, or as the case may be, the",
            "occurrence": "2",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch",
        },
        {
            "original": "the",
            "replacement": "the Accountant in Bankruptcy, or as the case may be, the",
            "occurrence": "1",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_after_anchor_single_ordinal_place_where_insert() -> None:
    subs = parse_fragment_substitution(
        "d in subsection (8), after \u201cany\u201d, in the second place where it occurs, "
        "insert \u201cinterim or final\u201d."
    )

    assert subs == [
        {
            "original": "any",
            "replacement": "any interim or final",
            "occurrence": "2",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_ordinal_place_there_shall_be_inserted() -> None:
    subs = parse_fragment_substitution(
        "a after the word \u201cperson\u2019s\u201d, in the first place where it occurs, "
        "there shall be inserted \u201c liability to income tax or \u201d ;"
    )

    assert subs == [
        {
            "original": "person\u2019s",
            "replacement": "person\u2019s liability to income tax or ",
            "occurrence": "1",
            "rule_id": "uk_effect_after_quoted_anchor_ordinal_places_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_where_ordinal_insert() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (2), after \u201csection\u201d where it first occurs "
        "insert \u201c or any other section \u201d ."
    )

    assert subs == [
        {
            "original": "section",
            "replacement": "section or any other section ",
            "occurrence": "1",
            "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_where_ordinal_there_inserted() -> None:
    subs = parse_fragment_substitution(
        "1 In section 5(1)(a), after \u201cor\u201d, where it second occurs, "
        "there is inserted \u201c on \u201d ."
    )

    assert subs == [
        {
            "original": "or",
            "replacement": "or on ",
            "occurrence": "2",
            "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_where_ordinal_nested_quote_insert() -> None:
    subs = parse_fragment_substitution(
        "i after \u201cassociation\u201d, where it first occurs, insert "
        "\u201c (in this Part, the \u201cprofessional association\u201d) \u201d ,"
    )

    assert subs == [
        {
            "original": "association",
            "replacement": "association (in this Part, the \u201cprofessional association\u201d) ",
            "occurrence": "1",
            "rule_id": "uk_effect_after_quoted_anchor_where_ordinal_nested_quote_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_prefixed_anchor_ordinal_insert() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph (b), after the second \u201corder\u201d insert "
        "\u201cand does not fall within paragraph (aa)\u201d ."
    )

    assert subs == [
        {
            "original": "order",
            "replacement": "order and does not fall within paragraph (aa)",
            "occurrence": "2",
            "rule_id": "uk_effect_after_prefixed_quoted_anchor_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_after_anchor_insert_to_definition() -> None:
    subs = parse_fragment_substitution(
        "b in the definition of \u201cqualified lawyer\u201d, after \u201c2007\u201d "
        "insert ; \u201cor a person who is a registered foreign lawyer\u201d"
    )

    assert subs == [
        {
            "original": (
                "TEXT_IN_DEFINITION_qualified lawyer\x1fAFTER\x1f2007"
            ),
            "replacement": "2007 or a person who is a registered foreign lawyer",
            "rule_id": "uk_effect_in_definition_after_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_comma_after_anchor_insert_to_definition() -> None:
    subs = parse_fragment_substitution(
        "21 In section 52(4), in the definition of \u201cindependent inland waterway undertaking\u201d, "
        "after \u201cof the Boards\u201d, insert \u201cor Canal & River Trust\u201d."
    )

    assert subs == [
        {
            "original": (
                "TEXT_IN_DEFINITION_independent inland waterway undertaking"
                "\x1fAFTER\x1fof the Boards"
            ),
            "replacement": "of the Boards or Canal & River Trust",
            "rule_id": "uk_effect_in_definition_after_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_nested_quote_after_anchor_insert_to_definition() -> None:
    subs = parse_fragment_substitution(
        "ii in the definition of \u201ccontributions\u201d after "
        "\u201cin respect of contributions\u201d insert \u201c(and accordingly, in the "
        "definition of \u201cthe Class 1 element\u201d given by this subsection, "
        "\u201cClass 1 contributions\u201d includes any interest or penalty in respect "
        "of Class 1 contributions)\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_IN_DEFINITION_contributions\x1fAFTER\x1fin respect of contributions",
            "replacement": (
                "in respect of contributions (and accordingly, in the definition "
                "of \u201cthe Class 1 element\u201d given by this subsection, "
                "\u201cClass 1 contributions\u201d includes any interest or penalty "
                "in respect of Class 1 contributions)"
            ),
            "rule_id": "uk_effect_in_definition_after_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_after_anchor_all_occurrence_insert_to_definition() -> None:
    subs = parse_fragment_substitution(
        "ii in the definition of \u201can action for removing from heritable property\u201d "
        "after \u201cdecree\u201d, in both places where it appears, insert \u201c, order\u201d, and"
    )

    assert subs == [
        {
            "original": (
                "TEXT_IN_DEFINITION_an action for removing from heritable property"
                "\x1fAFTER_EACH\x1fdecree"
            ),
            "replacement": "decree, order",
            "rule_id": "uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_before_anchor_insert_to_definition_child() -> None:
    subs = parse_fragment_substitution(
        "3 In section 336(1) (interpretation), in the definition of \u201cwaste\u201d "
        "at the end of paragraph (a), before the \u201cand\u201d insert "
        "\u201cas last amended by Council Regulation (EU) 2017/997,\u201d."
    )

    assert subs == [
        {
            "original": f"TEXT_IN_DEFINITION_CHILD_PARAGRAPH_waste{US}a{US}and",
            "replacement": "as last amended by Council Regulation (EU) 2017/997, and",
            "rule_id": "uk_effect_in_definition_child_before_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_with_parenthetical_aside() -> None:
    subs = parse_fragment_substitution(
        "7 In paragraph 21, after \u201c FA 2021 \u201d "
        "(as inserted by section 102(7) of FA 2021) "
        "insert \u201cand Schedule 11 to FA 2022,\u201d ."
    )

    assert subs == [
        {
            "original": " FA 2021 ",
            "replacement": " FA 2021 and Schedule 11 to FA 2022,",
            "rule_id": "uk_effect_after_quoted_anchor_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_block_insert() -> None:
    subs = parse_fragment_substitution(
        "2 In Part 1, after \u201cA National Crime Agency officer\u201d insert\u2014 "
        "A member of the Royal Navy Police."
    )

    assert subs == [
        {
            "original": "A National Crime Agency officer",
            "replacement": (
                "A National Crime Agency officer A member of the Royal Navy Police."
            ),
            "rule_id": "uk_effect_after_quoted_anchor_block_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_anchor_definition_entry_block_insert() -> None:
    subs = parse_fragment_substitution(
        "7 In section 47 (interpretation), after \u201cPart\u2014\u201d insert\u2014 "
        "\u201c central institution \u201d means\u2014 the Bank of England;"
    )

    assert subs == [
        {
            "original": "Part\u2014",
            "replacement": "Part\u2014 \u201c central institution \u201d means\u2014 the Bank of England;",
            "rule_id": "uk_effect_after_quoted_anchor_definition_entry_block_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_reference_section_insert() -> None:
    subs = parse_fragment_substitution(
        "1 In section 79(1) of the 1997 Act, after the reference to "
        "section 273 (offences by corporations) insert\u2014 \u201c section 275A "
        "(further provision as regards regulations: inquiries, etc.), \u201d ."
    )

    assert subs == [
        {
            "original": "section 273 (offences by corporations),",
            "replacement": (
                "section 273 (offences by corporations), section 275A "
                "(further provision as regards regulations: inquiries, etc.),"
            ),
            "rule_id": "uk_effect_after_reference_section_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_before_definition_insert() -> None:
    subs = parse_fragment_substitution(
        "a before the definition of \u201centitled to practise\u201d insert\u2014 "
        "\u201c Criminal Injuries Compensation Scheme \u201d means a compensation scheme; ;"
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_DEFINITION_entitled to practise",
            "replacement": (
                "\u201c Criminal Injuries Compensation Scheme \u201d means a compensation scheme; ;"
            ),
            "rule_id": "uk_effect_before_definition_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_unquoted_before_definition_insert() -> None:
    subs = parse_fragment_substitution(
        "2 In section 28(1), before the definition of members' code insert\u2014 "
        "\u201c member \u201d, in relation to the Scottish National Investment Bank p.l.c., "
        "means that company's directors; ."
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_DEFINITION_members' code",
            "replacement": (
                "\u201c member \u201d, in relation to the Scottish National Investment Bank p.l.c., "
                "means that company's directors;"
            ),
            "rule_id": "uk_effect_before_definition_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_does_not_flatten_entry_relating_to_schedule_insert() -> None:
    assert (
        parse_fragment_substitution(
            "12 before the entry relating to \u201cScottish Children's Reporter Administration\u201d "
            "insert\u2014 \u201c The Scottish Charity Regulator \u201d ."
        )
        == []
    )
    assert (
        parse_fragment_substitution(
            "4 after the entry relating to the Scottish Legal Aid Board "
            "insert\u2014 \u201c The Scottish Legal Complaints Commission \u201d ."
        )
        == []
    )


def test_parse_fragment_substitution_handles_before_definition_entry_insert() -> None:
    subs = parse_fragment_substitution(
        "a before the entry for \u201caction\u201d insert\u2014 "
        "\u201c the 2015 Act \u201d means the Welfare Funds (Scotland) Act 2015, ,"
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_DEFINITION_action",
            "replacement": (
                "\u201c the 2015 Act \u201d means the Welfare Funds (Scotland) Act 2015, ,"
            ),
            "rule_id": "uk_effect_before_definition_entry_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_definition_entry_insert() -> None:
    subs = parse_fragment_substitution(
        "b after the entry for \u201cthe Ombudsman\u201d insert\u2014 "
        "\u201c the Ombudsman's functions \u201d includes the Ombudsman's functions under the 2015 Act, ,"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_DEFINITION_the Ombudsman",
            "replacement": (
                "\u201c the Ombudsman's functions \u201d includes the Ombudsman's functions under the 2015 Act, ,"
            ),
            "rule_id": "uk_effect_after_definition_entry_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_does_not_treat_entity_entry_insert_as_definition() -> None:
    assert (
        parse_fragment_substitution(
            "4 after the entry for \u201cThe Royal Commission\u201d insert\u2014 "
            "\u201cThe Cairngorms National Park Authority\u201d."
        )
        == []
    )


def test_parse_fragment_substitution_handles_after_definitions_insert() -> None:
    subs = parse_fragment_substitution(
        "8 In section 31 (interpretation), in subsection (1), "
        "after the definitions of \u201cdirected\u201d and \u201cintrusive\u201d insert\u2014 "
        "\u201c joint surveillance operation \u201d means a case involving at least two police forces; ."
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_DEFINITION_intrusive",
            "replacement": (
                "\u201c joint surveillance operation \u201d means a case involving "
                "at least two police forces;"
            ),
            "rule_id": "uk_effect_after_definitions_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_for_insert_as_text_insertion() -> None:
    subs = parse_fragment_substitution("in paragraph 2(2), for \u201c6\u201d insert \u201c 12 \u201d")

    assert subs == [
        {
            "original": "6",
            "replacement": "6 12",
            "rule_id": "uk_effect_for_insert_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_for_there_is_inserted_as_replacement() -> None:
    subs = parse_fragment_substitution(
        "in subsection (3), for \u201c(h)\u201d there is inserted \u201c(i)\u201d."
    )

    assert subs == [
        {
            "original": "(h)",
            "replacement": "(i)",
            "rule_id": "uk_effect_for_there_is_inserted_replacement_text_patch",
        }
    ]


def test_parse_fragment_substitution_splits_compound_lettered_text_patches() -> None:
    subs = parse_fragment_substitution(
        "2 In section 13 of the 1990 Act, in subsection (1)\u2014 "
        "a for \u201cor (b)\u201d there is substituted \u201c,(b), (c) or (d)\u201d, "
        "and b after \u201cthis Part\u201d there is inserted "
        "\u201cor Part I of the Broadcasting Act 1996\u201d."
    )

    assert subs == [
        {
            "original": "or (b)",
            "replacement": ",(b), (c) or (d)",
            "rule_id": "uk_effect_compound_lettered_text_patch_instruction",
        },
        {
            "original": "this Part",
            "replacement": "this Part or Part I of the Broadcasting Act 1996",
            "rule_id": "uk_effect_compound_lettered_text_patch_instruction",
        },
    ]


def test_parse_fragment_substitution_handles_before_anchor_ordinal_insert() -> None:
    subs = parse_fragment_substitution(
        "ii before \u201cperiod\u201d, in the first place it occurs, insert \u201ccurrent\u201d , and"
    )

    assert subs == [
        {
            "original": "period",
            "replacement": "current period",
            "occurrence": "1",
            "rule_id": "uk_effect_before_quoted_anchor_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_beginning_insert() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (1), at the beginning insert \u201cSubject to section 88A,\u201d ;"
    )

    assert subs == [
        {
            "original": "TEXT_BEGINNING",
            "replacement": "Subject to section 88A,",
            "rule_id": "uk_effect_beginning_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_beginning_of_subsection_insert() -> None:
    subs = parse_fragment_substitution(
        "a at the beginning of subsection (1) insert \u201c Subject to subsection (4A), \u201d ,"
    )

    assert subs == [
        {
            "original": "TEXT_BEGINNING",
            "replacement": "Subject to subsection (4A),",
            "rule_id": "uk_effect_beginning_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_beginning_insert_with_carried_parent_context() -> None:
    subs = parse_fragment_substitution(
        "1 At the beginning of sub-paragraph (1) of paragraph 4 of that Schedule "
        "(giving effect to claims and amendments) there shall be inserted the "
        "words \u201c Subject to sub-paragraphs (1A) and (3) below, \u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_BEGINNING",
            "replacement": "Subject to sub-paragraphs (1A) and (3) below,",
            "rule_id": "uk_effect_beginning_carried_parent_context_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_preposed_at_end_insert() -> None:
    subs = parse_fragment_substitution(
        "1 In subsection (1A), there shall be inserted at the end the words "
        "\u201c and the amounts referred to in that subsection are net amounts\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "and the amounts referred to in that subsection are net amounts",
            "rule_id": "uk_effect_preposed_at_end_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_end_insert_with_carried_parent_context() -> None:
    subs = parse_fragment_substitution(
        "1 At the end of subsection (1) of section 9 of the Taxes Management Act "
        "1970 (as substituted by section 121(4) above) there shall be inserted "
        "the words \u201c but nothing in this subsection shall enable a self-assessment\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "but nothing in this subsection shall enable a self-assessment",
            "rule_id": "uk_effect_at_end_carried_parent_context_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_entry_for_of_insert() -> None:
    subs = parse_fragment_substitution(
        "Schedule 3 is amended by the insertion, after the entry for "
        "\u201cThe Royal Commission\u201d of \u201cThe Cairngorms National Park Authority\u201d."
    )

    assert subs == [
        {
            "original": "The Royal Commission",
            "replacement": "The Royal Commission The Cairngorms National Park Authority",
        }
    ]


def test_parse_fragment_substitution_handles_after_child_insert() -> None:
    subs = parse_fragment_substitution(
        "i after sub-paragraph (i) insert \u201cor\u201d; and"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_CHILD_subparagraph_i",
            "replacement": "or",
            "rule_id": "uk_effect_after_child_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_child_insert_with_comma() -> None:
    subs = parse_fragment_substitution(
        "ii after paragraph (a), insert \u201cand\u201d, and"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_CHILD_paragraph_a",
            "replacement": "and",
            "rule_id": "uk_effect_after_child_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_insert_after_child() -> None:
    subs = parse_fragment_substitution("a insert \u201c or \u201d after paragraph (a); and")

    assert subs == [
        {
            "original": "TEXT_AFTER_CHILD_paragraph_a",
            "replacement": "or",
            "rule_id": "uk_effect_after_child_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_compound_subsection_child_insert() -> None:
    subs = parse_fragment_substitution(
        "a after subsection (4)(a)(i), insert \u201c or \u201d ;"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_CHILD_subparagraph_i",
            "replacement": "or",
            "rule_id": "uk_effect_after_compound_subsection_child_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_omit_words_to_end() -> None:
    subs = parse_fragment_substitution(
        '3 In subsection (4) omit the words from “; and references” to the end.'
    )

    assert subs == [
        {
            "original": "TEXT_FROM_; and references_TO_END",
            "replacement": "",
        }
    ]


def test_parse_fragment_substitution_handles_words_after_anchor_substitution() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (2), for the words after \u201cmore than\u201d "
        "substitute \u201c6 months or 12 months\u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_more than_TO_END",
            "replacement": "6 months or 12 months",
            "rule_id": "uk_effect_after_anchor_to_end_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_words_following_anchor_substitution() -> None:
    subs = parse_fragment_substitution(
        "4 In the heading of Part 3, for the words following \u201cScotland\u201d "
        "substitute \u201c or Northern Ireland.\u201d"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_Scotland_TO_END",
            "replacement": "or Northern Ireland.",
            "rule_id": "uk_effect_after_anchor_to_end_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_scopes_definition_child_tail_anchor_substitution() -> None:
    subs = parse_fragment_substitution(
        "a in the definition of \u201cextended sentence\u201d, in the words following "
        "paragraph (b), for the words following \u201cScotland\u201d substitute "
        "\u201c or Northern Ireland \u201d ;"
    )

    assert subs == [
        {
            "original": (
                f"TEXT_IN_DEFINITION_CHILD_TAIL{US}extended sentence{US}paragraph"
                f"{US}b{US}AFTER{US}Scotland{US}TO_END"
            ),
            "replacement": "or Northern Ireland",
            "rule_id": "uk_effect_definition_child_tail_after_anchor_to_end_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_quoted_words_anchor_to_end_substitution() -> None:
    subs = parse_fragment_substitution(
        "9 In section 65(5), for the words \u201cis a man\u201d to the end substitute "
        "\u201cis a person who dies leaving a surviving spouse\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_is a man_TO_END",
            "replacement": "is a person who dies leaving a surviving spouse",
            "rule_id": "uk_effect_quoted_words_anchor_to_end_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_omit_words_after_anchor() -> None:
    subs = parse_fragment_substitution(
        "a in paragraph (b) omit the words after \u201csource\u201d, and"
    )

    assert subs == [
        {
            "original": "TEXT_AFTER_source_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_after_anchor_to_end_omission_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_missing_space_before_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        "4 In subsection (4), for the words \u201cneglecting or refusing to pay\u201d"
        "there shall be substituted the words \u201cin default\u201d."
    )

    assert subs == [
        {
            "original": "neglecting or refusing to pay",
            "replacement": "in default",
            "rule_id": "uk_effect_missing_space_there_is_substituted_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_dangling_passive_substitution_quote() -> None:
    subs = parse_fragment_substitution(
        "c in sub-paragraph (4), for the words \u201cMedical Practices Committee\u201d "
        "there is substituted \u201cHealth Board;"
    )

    assert subs == [
        {
            "original": "Medical Practices Committee",
            "replacement": "Health Board;",
            "rule_id": "uk_effect_dangling_passive_substitution_quote_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_dangling_active_substitution_quote() -> None:
    subs = parse_fragment_substitution(
        "250 In section 31C(2)(b) (appeals: appeals to Special Commissioners) "
        "for \u201csection 350 of the principal Act\u201d substitute "
        "\u201csection 963(3) of ITA 2007."
    )

    assert subs == [
        {
            "original": "section 350 of the principal Act",
            "replacement": "section 963(3) of ITA 2007.",
            "rule_id": "uk_effect_dangling_active_substitution_quote_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_all_occurrences_passive_with_words_marker() -> None:
    subs = parse_fragment_substitution(
        "2 for the word \u201cassessment\u201d, in each place where it occurs, "
        "there shall be substituted the words \u201c amendment or assessment \u201d ."
    )

    assert subs == [
        {
            "original": "assessment",
            "replacement": " amendment or assessment ",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_words_before_child_substitution() -> None:
    subs = parse_fragment_substitution(
        "i for the words before paragraph (a), substitute "
        "\u201cThe minimum term must be adjusted, taking into account\u2014\u201d ;"
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_CHILD_paragraph_a",
            "replacement": "The minimum term must be adjusted, taking into account\u2014",
            "rule_id": "uk_effect_before_child_text_substitution_patch",
        }
    ]


def test_parse_fragment_substitution_handles_unquoted_words_before_child_block() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (6) for the words before paragraph (a) substitute\u2014 "
        "6 If, on an appeal notified to the tribunal, the tribunal decides\u2014 ."
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_CHILD_paragraph_a",
            "replacement": "If, on an appeal notified to the tribunal, the tribunal decides\u2014",
            "rule_id": "uk_effect_before_child_block_text_substitution_patch",
        }
    ]


def test_parse_fragment_substitution_does_not_strip_different_before_child_block_label() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (6) for the words before paragraph (a) substitute\u2014 "
        "7 If, on an appeal notified to the tribunal, the tribunal decides\u2014 ."
    )

    assert subs == [
        {
            "original": "TEXT_BEFORE_CHILD_paragraph_a",
            "replacement": "7 If, on an appeal notified to the tribunal, the tribunal decides\u2014",
            "rule_id": "uk_effect_before_child_block_text_substitution_patch",
        }
    ]


def test_parse_fragment_substitution_handles_omit_quoted_range() -> None:
    subs = parse_fragment_substitution(
        "ii in paragraph (b), omit the words from \u201c(ignoring\u201d to \u201cthat Act)\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_(ignoring_TO_that Act)",
            "replacement": "",
            "rule_id": "uk_effect_omit_quoted_range_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_omit_words_from_second_place_to_end() -> None:
    subs = parse_fragment_substitution(
        "i in subsection (4), the words from \u201cand\u201d in the second place where it "
        "occurs to the end are repealed; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_and_TO_END",
            "replacement": "",
            "occurrence": "2",
        }
    ]


def test_parse_fragment_substitution_handles_words_from_anchor_onwards_omitted() -> None:
    subs = parse_fragment_substitution(
        "a in sub-paragraph (2), the words from \u201cand shall include\u201d onwards "
        "shall be omitted; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_and shall include_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_range_to_end_passive_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_words_from_anchor_onwards_passive_substitution() -> None:
    subs = parse_fragment_substitution(
        "5 In subsection (8), for the words from \u201cpayable\u201d onwards there shall "
        "be substituted \u201c the cash bid \u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_payable_TO_END",
            "replacement": "the cash bid",
            "rule_id": "uk_effect_range_to_end_there_is_substituted_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_to_end_quoted_dash_substitution() -> None:
    subs = parse_fragment_substitution(
        "iii in sub-paragraph (8), for the words from \u201cthere\u201d to the end "
        "of the sub-paragraph substitute\u2014 \u201c the things mentioned occur. \u201d ,"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_there_TO_END",
            "replacement": "the things mentioned occur.",
            "rule_id": "uk_effect_range_to_end_quoted_dash_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_joined_there_shall_range_to_end_substitution() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (1), for the words \u201cthe collector shall\u201d onwards "
        "thereshall be substituted the words \u201cthe collector may distrain\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_the collector shall_TO_END",
            "replacement": "the collector may distrain",
            "rule_id": "uk_effect_range_to_end_there_is_substituted_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_joined_be_omitted_range_to_end_repeal() -> None:
    subs = parse_fragment_substitution(
        "d the words from \u201cThe costs\u201d onwards shall beomitted."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_The costs_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_range_to_end_passive_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_joined_be_omitted_quoted_range_repeal() -> None:
    subs = parse_fragment_substitution(
        "d the words from \u201cThe costs\u201d to \u201cthe collector, and\u201d shall beomitted."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_The costs_TO_the collector, and",
            "replacement": "",
            "rule_id": "uk_effect_range_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_cease_effect_quoted_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (3), the words \u201cthe following provisions of\u201d "
        "shall cease to have effect."
    )

    assert subs == [
        {
            "original": "the following provisions of",
            "replacement": "",
            "rule_id": "uk_effect_cease_effect_quoted_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_cease_effect_range_to_end_repeal() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (3), the words from \u201cand the references\u201d to the end "
        "shall cease to have effect."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_and the references_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_cease_effect_range_to_end_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        'a in subsections (1), (4) and (7), for “the Director” there is substituted “the OFT”;'
    )

    assert subs == [
        {
            "original": "the Director",
            "replacement": "the OFT",
        }
    ]


def test_parse_fragment_substitution_handles_in_both_places_before_substitute() -> None:
    subs = parse_fragment_substitution(
        "i for \u201cPart 8 of the 2011 Measure\u201d in both places where it occurs, "
        "substitute \u201cPart 5A of the 2013 Act\u201d;"
    )

    assert subs == [
        {
            "original": "Part 8 of the 2011 Measure",
            "replacement": "Part 5A of the 2013 Act",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_in_both_places_where_it_appears() -> None:
    subs = parse_fragment_substitution(
        "for \u201cexit day\u201d, in both places where it appears, substitute \u201cIP completion day\u201d."
    )

    assert subs == [
        {
            "original": "exit day",
            "replacement": "IP completion day",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_wherever_it_appears() -> None:
    subs = parse_fragment_substitution(
        "for \u201cexit day\u201d, wherever it appears, substitute \u201cIP completion day\u201d,"
    )

    assert subs == [
        {
            "original": "exit day",
            "replacement": "IP completion day",
        }
    ]


def test_parse_fragment_substitution_tolerates_extra_quote_after_in_both_places() -> None:
    subs = parse_fragment_substitution(
        "i for \u201cPart 8\u201d in both places where it occurs\u201d substitute \u201cPart 5A\u201d ;"
    )

    assert subs == [
        {
            "original": "Part 8",
            "replacement": "Part 5A",
        }
    ]


def test_parse_fragment_substitution_tolerates_closing_quote_as_opener_after_substitute() -> None:
    subs = parse_fragment_substitution(
        "i for \u201cPanel\u201d, in both places where it occurs, substitute \u201dCommission\u201d;"
    )

    assert subs == [
        {
            "original": "Panel",
            "replacement": "Commission",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_for_the_words_substitute() -> None:
    subs = parse_fragment_substitution(
        "11 In paragraph 4(6), for the words \u201cMental Health (Scotland) Act 1984 "
        "(c. 36)\u201d substitute \u201cMental Health (Care and Treatment) "
        "(Scotland) Act 2003 (asp 13)\u201d."
    )

    assert subs == [
        {
            "original": "Mental Health (Scotland) Act 1984 (c. 36)",
            "replacement": "Mental Health (Care and Treatment) (Scotland) Act 2003 (asp 13)",
        }
    ]


def test_parse_fragment_substitution_handles_child_qualified_quoted_substitution() -> None:
    subs = parse_fragment_substitution(
        "8 In schedule 2, for the words \u201cScottish Homes\u201d in paragraph 44 "
        "substitute \u201c The Scottish Housing Regulator \u201d ."
    )

    assert subs == [
        {
            "original": "Scottish Homes",
            "replacement": "The Scottish Housing Regulator",
            "source_child_kind": "paragraph",
            "source_child_label": "44",
            "rule_id": "uk_effect_child_qualified_quoted_substitution_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_range_from_first_occurrence() -> None:
    subs = parse_fragment_substitution(
        "a in sub-paragraph (b), for the words from \u201ca\u201d where it first occurs "
        "to \u201c(c.41)\u201d substitute \u201c an employee of a relevant authority \u201d ; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_a_TO_(c.41)",
            "replacement": "an employee of a relevant authority",
            "occurrence": "1",
            "rule_id": "uk_effect_range_occurrence_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_comma_where_it_occurs() -> None:
    subs = parse_fragment_substitution(
        "a for the words from \u201cshall\u201d, where it second occurs, to "
        "\u201cwhether\u201d substitute \u201cif\u201d, and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_shall_TO_whether",
            "replacement": "if",
            "occurrence": "2",
            "rule_id": "uk_effect_range_occurrence_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_comma_before_substitute() -> None:
    subs = parse_fragment_substitution(
        "for the words from \u201cmeans\u2014\u201d to \u201c(and\u201d, substitute \u201creplacement\u201d"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_means\u2014_TO_(and",
            "replacement": "replacement",
            "rule_id": "uk_effect_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_range_there_shall_be_substituted() -> None:
    subs = parse_fragment_substitution(
        "2 In subsection (1), for the words from \u201cindependent\u201d to "
        "\u201c84(1)(d), (e) or (f)\u201d there shall be substituted "
        "\u201c relevant regulated radio service \u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_independent_TO_84(1)(d), (e) or (f)",
            "replacement": "relevant regulated radio service",
            "rule_id": "uk_effect_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_same_anchor_adjacent_occurrence_range() -> None:
    subs = parse_fragment_substitution(
        "ii for the words from \u201cobjectives\u201d, where it first occurs, to "
        "\u201cobjectives\u201d, where it second occurs, substitute \u201c authority's plan \u201d , and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_objectives_TO_objectives",
            "replacement": "authority's plan",
            "occurrence": "1",
            "rule_id": "uk_effect_same_anchor_adjacent_occurrence_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_to_end_of_subsection() -> None:
    subs = parse_fragment_substitution(
        "for the words from \u201cAct\u2014\u201d to the end of the subsection, substitute "
        "\u201creplacement text\u201d"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_Act\u2014_TO_END",
            "replacement": "replacement text",
        }
    ]


def test_parse_fragment_substitution_preserves_range_to_end_second_occurrence() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1), for the words from \u201cthe\u201d where it second occurs "
        "to the end substitute \u201c any of the persons mentioned in subsection (1A) "
        "may grant authorisations \u201d , and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_the_TO_END",
            "replacement": (
                "any of the persons mentioned in subsection (1A) may grant "
                "authorisations"
            ),
            "occurrence": "2",
        }
    ]


def test_parse_fragment_substitution_preserves_labeled_paragraph_end_range_suffix() -> None:
    subs = parse_fragment_substitution(
        "i for the words from \u201cshall\u201d to the end of paragraph (b) substitute "
        "\u201c may \u201d ,"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_shall_TO_END",
            "replacement": "may",
            "target_suffix_kind": "paragraph",
            "target_suffix_label": "b",
            "rule_id": "uk_effect_labeled_end_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_labeled_paragraph_end_range_suffix() -> None:
    subs = parse_fragment_substitution(
        "7 In subsection (7), for the words from \u201ca failure\u201d to the end of "
        "paragraph (c) there shall be substituted \u201c a disqualification \u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_a failure_TO_END",
            "replacement": "a disqualification",
            "target_suffix_kind": "paragraph",
            "target_suffix_label": "c",
            "rule_id": "uk_effect_labeled_end_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_labeled_end_range_block() -> None:
    subs = parse_fragment_substitution(
        "a for the words from \u201ca list\u201d to the end of paragraph (a) "
        "substitute \u2014 a in relation to a list published in accordance with "
        "regulations, the first part of the list; ;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_a list_TO_END",
            "replacement": (
                "a in relation to a list published in accordance with regulations, "
                "the first part of the list; ;"
            ),
            "target_suffix_kind": "paragraph",
            "target_suffix_label": "a",
            "rule_id": "uk_effect_labeled_end_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_labeled_end_range_ordinal_comma_block() -> None:
    subs = parse_fragment_substitution(
        "8 In subsection (1), for the words from \u201cpetition\u201d, where it first "
        "occurs, to the end of paragraph (a), substitute debtor application is "
        "made, the Accountant in Bankruptcy shall award sequestration forthwith "
        "if he is satisfied\u2014 a that the application has been made in accordance "
        "with the provisions of this Act; ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_petition_TO_END",
            "replacement": (
                "debtor application is made, the Accountant in Bankruptcy shall "
                "award sequestration forthwith if he is satisfied\u2014 a that the "
                "application has been made in accordance with the provisions of this Act;"
            ),
            "target_suffix_kind": "paragraph",
            "target_suffix_label": "a",
            "occurrence": "1",
            "rule_id": "uk_effect_labeled_end_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_range_with_words_wrapper() -> None:
    subs = parse_fragment_substitution(
        "c for the words from \u201ctwo or more inhabitants of the parish\u201d to "
        "\u201csufficient persons\u201d there shall be substituted the words "
        "\u201cone or more independent persons appointed by the collector\u201d, and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_two or more inhabitants of the parish_TO_sufficient persons",
            "replacement": "one or more independent persons appointed by the collector",
            "rule_id": "uk_effect_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_unquoted_range_start_occurrence_block() -> None:
    subs = parse_fragment_substitution(
        "1 for the words from \u201cthe\u201d, where it second occurs, to "
        "\u201cprescribed\u201d substitute \u2014 a the chairman of a Health Board, ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_the_TO_prescribed",
            "replacement": "a the chairman of a Health Board,",
            "occurrence": "2",
            "rule_id": "uk_effect_range_where_ordinal_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_unquoted_range_independent_end_occurrence_block() -> None:
    subs = parse_fragment_substitution(
        "6 for the words from \u201cthe\u201d, where it first occurs, to "
        "\u201ctrustee\u201d, where it second occurs, substitute\u2014 a a trustee is appointed ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_the_TO_trustee",
            "replacement": "a a trustee is appointed",
            "occurrence": "1",
            "end_occurrence": "2",
            "rule_id": "uk_effect_range_independent_end_occurrence_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_to_end_first_appears_block() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (6), for the words from \u201cthe\u201d where it first appears "
        "to the end substitute\u2014 a the Public Services Reform (Scotland) Act 2010 "
        "have the same meanings in that subsection as in that Act; ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_the_TO_END",
            "replacement": (
                "a the Public Services Reform (Scotland) Act 2010 have the same "
                "meanings in that subsection as in that Act;"
            ),
            "occurrence": "1",
            "rule_id": "uk_effect_range_to_end_ordinal_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_comma_ordinal_range_to_end_block() -> None:
    subs = parse_fragment_substitution(
        "ii for the words from \u201clist\u201d, where it second occurs, to the end "
        "substitute list\u2014 a in relation to a list referred to in subsection "
        "(8)(a), perform; ;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_list_TO_END",
            "replacement": (
                "list\u2014 a in relation to a list referred to in subsection (8)(a), perform; ;"
            ),
            "occurrence": "2",
            "rule_id": "uk_effect_range_to_end_ordinal_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_to_end_open_quote_block() -> None:
    subs = parse_fragment_substitution(
        "in sub-paragraph (3), for the words from \u201c, any of the following "
        "provisions of the ANO 2016\u201d to the end, substitute \u201c\u2014 a article "
        "265E(2)(b)(ii) of the ANO 2016; b regulation 3(5)(b). ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_, any of the following provisions of the ANO 2016_TO_END",
            "replacement": (
                "a article 265E(2)(b)(ii) of the ANO 2016; b regulation 3(5)(b)."
            ),
            "rule_id": "uk_effect_range_to_end_open_quote_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_preserves_definition_context_for_range_to_end() -> None:
    subs = parse_fragment_substitution(
        "a in the definition of \u201cmental disorder\u201d, for the words from "
        "\u201cmeans\u201d to the end substitute \u201chas the meaning given by section 328\u201d ;"
    )

    assert subs[0] == {
        "original": "TEXT_IN_DEFINITION_mental disorder\x1fFROM\x1fmeans\x1fTO_END",
        "replacement": "has the meaning given by section 328",
        "rule_id": "uk_effect_definition_range_to_end_substitution_text_patch",
    }


def test_parse_fragment_substitution_preserves_unquoted_definition_range_to_end() -> None:
    subs = parse_fragment_substitution(
        "in the definition of registered independent health care services, "
        "for the words from \u201csection 2(5)\u201d to the end of the definition "
        "substitute \u201csection 10E of the National Health Service (Scotland) "
        "Act 1978 (c. 29)) registered under section 10P of that Act;\u201d ."
    )

    assert subs[0] == {
        "original": (
            "TEXT_IN_DEFINITION_registered independent health care services"
            "\x1fFROM\x1fsection 2(5)\x1fTO_END"
        ),
        "replacement": (
            "section 10E of the National Health Service (Scotland) Act 1978 "
            "(c. 29)) registered under section 10P of that Act;"
        ),
        "rule_id": "uk_effect_unquoted_definition_range_to_end_substitution_text_patch",
    }


def test_parse_fragment_substitution_preserves_definition_range_to_end_occurrence() -> None:
    subs = parse_fragment_substitution(
        "in the definition of \u201cjoint fire board\u201d for the words from "
        "\u201cboard\u201d, where it secondly occurs, to the end substitute "
        "\u201cand rescue board constituted by an amalgamation scheme\u201d ."
    )

    assert subs[0] == {
        "original": "TEXT_IN_DEFINITION_joint fire board\x1fFROM\x1fboard\x1fTO_END",
        "replacement": "and rescue board constituted by an amalgamation scheme",
        "occurrence": "2",
        "rule_id": "uk_effect_definition_range_to_end_occurrence_substitution_text_patch",
    }


def test_parse_fragment_substitution_handles_unprefixed_anchor_to_end_substitution() -> None:
    subs = parse_fragment_substitution(
        "ii in sub-paragraph (2)(a), from \u201csection 28\u201d to the end substitute "
        "\u201cany of the provisions mentioned in sub-paragraph (1)(a);\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_section 28_TO_END",
            "replacement": "any of the provisions mentioned in sub-paragraph (1)(a);",
            "rule_id": "uk_effect_anchor_to_end_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_opening_words_substitution() -> None:
    subs = parse_fragment_substitution(
        "i for the opening words substitute \u201cRegulations under subsection (2) may make provision\u2014\u201d,"
    )

    assert subs == [
        {
            "original": "TEXT_OPENING_WORDS",
            "replacement": "Regulations under subsection (2) may make provision\u2014",
            "rule_id": "uk_effect_opening_words_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_end_of_that_paragraph_insert() -> None:
    subs = parse_fragment_substitution(
        "b at the end of that paragraph insert \u201cor is Scottish Water,\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "or is Scottish Water,",
            "rule_id": "uk_effect_at_end_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_end_of_definition_insert() -> None:
    subs = parse_fragment_substitution(
        "6 In section 23(1), at the end of the definition of \u201cperson aggrieved\u201d "
        "insert \u201cor (as the case may be) section 6A(5)\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_IN_DEFINITION_person aggrieved\x1fAT_END",
            "replacement": "or (as the case may be) section 6A(5)",
            "rule_id": "uk_effect_in_definition_at_end_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_end_comma_insert() -> None:
    subs = parse_fragment_substitution(
        "In the heading, at the end, insert \u201cand the constitutional status of Northern Ireland\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "and the constitutional status of Northern Ireland",
            "rule_id": "uk_effect_at_end_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_insert_at_end_reverse_order() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph (c) insert at the end \u201cor on shared equity terms,\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "or on shared equity terms,",
            "rule_id": "uk_effect_at_end_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_insert_text_at_end_reverse_order() -> None:
    subs = parse_fragment_substitution(
        "i insert \u201c or \u201d at the end of sub-paragraph (ii);"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "or",
            "rule_id": "uk_effect_insert_text_at_end_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_end_new_line_unquoted_insert() -> None:
    subs = parse_fragment_substitution(
        "2 In section 12AA(6), at the end (and on a new line) insert\u2014 "
        "But see section 12ABZA."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "But see section 12ABZA",
            "rule_id": "uk_effect_at_end_unquoted_text_insertion_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_word_inserted_at_end() -> None:
    subs = parse_fragment_substitution(
        "a the word \u201cand\u201d shall be inserted at the end of paragraph (a); and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_END",
            "replacement": "and",
            "rule_id": "uk_effect_passive_insert_text_at_end_patch",
        }
    ]


def test_parse_fragment_substitution_handles_direct_words_are_repealed() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph 2(1), the words \u201cor section 66 of this Act\u201d are repealed."
    )

    assert subs == [
        {
            "original": "or section 66 of this Act",
            "replacement": "",
        }
    ]


def test_parse_fragment_substitution_handles_repeal_quoted_words() -> None:
    subs = parse_fragment_substitution(
        "c repeal the words \u201cor to the Scottish Crime and Drug Enforcement Agency\u201d."
    )

    assert subs == [
        {
            "original": "or to the Scottish Crime and Drug Enforcement Agency",
            "replacement": "",
            "rule_id": "uk_effect_repeal_quoted_words_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_omit_the_words() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph (c), omit the words \u201cin other respects,\u201d."
    )

    assert subs == [
        {
            "original": "in other respects,",
            "replacement": "",
            "rule_id": "uk_effect_direct_quoted_word_omission_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_leave_out_and_insert() -> None:
    subs = parse_fragment_substitution(
        "i leave out \u201ca solicitor\u201d and insert \u201c a practising solicitor \u201d ,"
    )

    assert subs == [
        {
            "original": "a solicitor",
            "replacement": "a practising solicitor",
            "rule_id": "uk_effect_leave_out_and_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_immediately_before_word_insert() -> None:
    subs = parse_fragment_substitution(
        "3 In section 12(2)(a), immediately before the word \u201cAudit\u201d "
        "insert \u201c Public \u201d ."
    )

    assert subs == [
        {
            "original": "Audit",
            "replacement": "Public Audit",
            "rule_id": "uk_effect_immediately_before_word_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_last_occurrence_insert() -> None:
    subs = parse_fragment_substitution(
        "ii after \u201ccaution\u201d, where last occurring, insert \u201c or to give such other security \u201d ,"
    )

    assert subs == [
        {
            "original": "caution",
            "replacement": "caution or to give such other security ",
            "occurrence": "-1",
            "rule_id": "uk_effect_after_quoted_anchor_last_occurrence_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_immediately_before_word_ordinal_insert() -> None:
    subs = parse_fragment_substitution(
        "a in paragraph 1, immediately before the word \u201cAudit\u201d, "
        "where it occurs for the second time, insert \u201c Public \u201d ,"
    )

    assert subs == [
        {
            "original": "Audit",
            "replacement": "Public Audit",
            "occurrence": "2",
            "rule_id": "uk_effect_immediately_before_word_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_direct_quoted_word_omission_at_end() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (10)(a), omit the \u201cor\u201d at the end;"
    )

    assert subs == [
        {
            "original": "or",
            "replacement": "",
            "rule_id": "uk_effect_direct_quoted_word_omission_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_all_occurrences_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "d in subsection (6), the word \u201cqualifying\u201d in each place where it "
        "occurs is repealed,"
    )

    assert subs == [
        {
            "original": "qualifying",
            "replacement": "",
            "rule_id": "uk_effect_all_occurrences_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_ordinal_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1), the word \u201cqualifying\u201d in the first place where "
        "it occurs is repealed,"
    )

    assert subs == [
        {
            "original": "qualifying",
            "replacement": "",
            "occurrence": "1",
            "rule_id": "uk_effect_ordinal_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_final_bare_quoted_word_repeal() -> None:
    subs = parse_fragment_substitution('a the “and” at the end of paragraph (aa) is repealed;')

    assert subs == [
        {
            "original": "and",
            "replacement": "",
            "occurrence": "-1",
            "rule_id": "uk_effect_final_bare_quoted_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_entry_repeal() -> None:
    subs = parse_fragment_substitution(
        "iv the definition of \u201cquality contract\u201d is repealed"
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_quality contract",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_entry_shall_be_omitted() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (5), the definition of \u201cthe Commission\u201d shall be omitted."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_the Commission",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_imperative_definition_entry_repeal() -> None:
    subs = parse_fragment_substitution(
        "a omit the definition of \u201cclinical commissioning group\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_clinical commissioning group",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_multiple_imperative_definition_entry_repeals() -> None:
    subs = parse_fragment_substitution(
        "a omit the definitions of \u201cbuilding safety risk\u201d and \u201crelevant risk\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_building safety risk",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
        {
            "original": "TEXT_DEFINITION_ENTRY_relevant risk",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_multiple_declarative_definition_entry_repeals() -> None:
    subs = parse_fragment_substitution(
        "3 In section 28(1)(interpretation), the definitions of "
        "\u201cUnited Kingdom national\u201d and \u201cUnited Kingdom resident\u201d are repealed."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_United Kingdom national",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
        {
            "original": "TEXT_DEFINITION_ENTRY_United Kingdom resident",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_definition_entry_substitution() -> None:
    subs = parse_fragment_substitution(
        "b for the definition of \u201cmedical devices provision\u201d substitute\u2014 "
        "\u201c medical devices provision \u201d, in Chapter 1, has the meaning given by section 17(2); ."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_medical devices provision",
            "replacement": (
                "\u201c medical devices provision \u201d, in Chapter 1, "
                "has the meaning given by section 17(2);"
            ),
            "rule_id": "uk_effect_definition_entry_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_anchor_comma() -> None:
    assert parse_fragment_substitution(
        "a after the definition of \u201cthe 1988 Act\u201d, insert\u2014 "
        "\u201c the 2016 Act \u201d means the Private Housing (Tenancies) (Scotland) Act 2016, ,"
    ) == [
        {
            "original": "TEXT_AFTER_DEFINITION_the 1988 Act",
            "replacement": (
                "\u201c the 2016 Act \u201d means the Private Housing "
                "(Tenancies) (Scotland) Act 2016, ,"
            ),
            "rule_id": "uk_effect_after_definition_text_insertion_patch",
        }
    ]
    assert parse_fragment_substitution(
        "a after the definition of the \u201c2002 Act\u201d insert\u2014 "
        "\u201cthe 2011 Regulations\u201d means the Civil Jurisdiction and Judgments "
        "(Maintenance) Regulations 2011 ( S.I. 2011/1484 ); ;"
    ) == [
        {
            "original": "TEXT_AFTER_DEFINITION_2002 Act",
            "replacement": (
                "\u201cthe 2011 Regulations\u201d means the Civil Jurisdiction and "
                "Judgments (Maintenance) Regulations 2011 ( S.I. 2011/1484 ); ;"
            ),
            "rule_id": "uk_effect_after_definition_text_insertion_patch",
        }
    ]
    assert parse_fragment_substitution(
        "6 In section 111, for the definition of \u201cregistered social landlord\u201d, "
        "substitute\u2014 \u201c registered social landlord \u201d means a body registered in the register, ."
    ) == [
        {
            "original": "TEXT_DEFINITION_ENTRY_registered social landlord",
            "replacement": "\u201c registered social landlord \u201d means a body registered in the register,",
            "rule_id": "uk_effect_definition_entry_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_child_repeal() -> None:
    subs = parse_fragment_substitution(
        "3 In section 42, in subsection (2), in the definition of "
        "\u201crelevant provision\u201d, omit paragraph (d)."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_CHILD_PARAGRAPH_relevant provision\x1fd",
            "replacement": "",
            "rule_id": "uk_effect_definition_child_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_child_repeal_without_comma() -> None:
    subs = parse_fragment_substitution(
        "3 In section 128(7) in the definition of "
        "\u201cprimary legislation\u201d omit paragraph (b)."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_CHILD_PARAGRAPH_primary legislation\x1fb",
            "replacement": "",
            "rule_id": "uk_effect_definition_child_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_definition_child_substitution() -> None:
    subs = parse_fragment_substitution(
        "c in the definition of \u201creview partner\u201d, for paragraph (c) "
        "substitute\u2014 an integrated care board, or ."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_CHILD_PARAGRAPH_review partner\x1fc",
            "replacement": "an integrated care board, or",
            "rule_id": "uk_effect_definition_child_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_contextual_preceding_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "i the word \u201cand\u201d immediately preceding paragraph (b) is repealed,"
    )

    assert subs == [
        {
            "original": "TEXT_WORD_and_IMMEDIATELY_PRECEDING_paragraph_b",
            "replacement": "",
            "rule_id": "uk_effect_contextual_adjacent_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_contextual_following_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "a the word \u201cand\u201d which follows paragraph (c) is repealed,"
    )

    assert subs == [
        {
            "original": "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_c",
            "replacement": "",
            "rule_id": "uk_effect_contextual_adjacent_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_immediate_follows_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "ii the word \u201cor\u201d which immediately follows paragraph (b) is repealed; and"
    )

    assert subs == [
        {
            "original": "TEXT_WORD_or_IMMEDIATELY_FOLLOWING_paragraph_b",
            "replacement": "",
            "rule_id": "uk_effect_contextual_adjacent_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_contextual_target_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "ii the word \u201cand\u201d immediately following subsection (4)(a) is repealed, and"
    )

    assert subs == [
        {
            "original": "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_paragraph_a",
            "replacement": "",
            "rule_id": "uk_effect_contextual_nested_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_nested_paragraph_contextual_word_repeal() -> None:
    subs = parse_fragment_substitution(
        "v the word \u201cand\u201d immediately following paragraph (c)(ii) is repealed,"
    )

    assert subs == [
        {
            "original": "TEXT_WORD_and_IMMEDIATELY_FOLLOWING_subparagraph_ii",
            "replacement": "",
            "rule_id": "uk_effect_contextual_nested_word_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_final_quoted_word_omission() -> None:
    subs = parse_fragment_substitution("a in paragraph (a), omit the final \u201cand\u201d;")

    assert subs == [
        {
            "original": "and",
            "replacement": "",
            "occurrence": "-1",
            "rule_id": "uk_effect_final_quoted_word_omit_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_after_second_insert() -> None:
    subs = parse_fragment_substitution(
        "ii in subsection (6), after second \u201csection\u201d insert \u201c 14A(9) or \u201d."
    )

    assert subs == [
        {
            "original": "section",
            "replacement": "section 14A(9) or ",
            "occurrence": "2",
            "rule_id": "uk_effect_after_prefixed_quoted_anchor_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_parenthetical_before_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        'a for “the Director” (in each place) there is substituted “the OFT”;'
    )

    assert subs == [
        {
            "original": "the Director",
            "replacement": "the OFT",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_there_shall_be_substituted() -> None:
    subs = parse_fragment_substitution(
        'b for the words “Act concerned” there shall be substituted “1998 Act”.'
    )

    assert subs == [
        {
            "original": "Act concerned",
            "replacement": "1998 Act",
        }
    ]


def test_parse_fragment_substitution_handles_preposed_there_shall_be_substituted() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (5) there shall be substituted for the words "
        "“or 8 above” the words “ or regulation 4(a) of the General Food "
        "Regulations 2004 ” ."
    )

    assert subs == [
        {
            "original": "or 8 above",
            "replacement": "or regulation 4(a) of the General Food Regulations 2004",
            "rule_id": "uk_effect_preposed_passive_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_to_substituted() -> None:
    subs = parse_fragment_substitution(
        '8 In subsection (13) for the words from the beginning to “in Northern Ireland,” '
        'substitute “ “Northern Ireland Social Security Commissioner” means”.'
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_in Northern Ireland,",
            "replacement": "“Northern Ireland Social Security Commissioner” means",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_passive_substitution() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1) for the words from the beginning of the subsection "
        "to \u201ca person\u201d are substituted the words "
        "\u201cFor the purposes of the Enterprise Act 2002, a person\u201d; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_a person",
            "replacement": "For the purposes of the Enterprise Act 2002, a person",
            "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_active_comma_substitution() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (2), for the words from the beginning to \u201ctaken\u201d, "
        "substitute \u201c Those steps are \u201d ,"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_taken",
            "replacement": "Those steps are",
            "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_there_shall_be_substituted() -> None:
    subs = parse_fragment_substitution(
        "3 In subsection (6), for the words from the beginning to \u201cshall be made\u201d "
        "there shall be substituted \u201c An application shall be made \u201d ."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_shall be made",
            "replacement": "An application shall be made",
            "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_omission() -> None:
    subs = parse_fragment_substitution(
        "5 In subsection (2), omit from the beginning to \u201ctaxpayer; and\u201d."
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_taxpayer; and",
            "replacement": "",
            "rule_id": "uk_effect_from_beginning_omission_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_from_the_words_from_beginning_to_substituted() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1), from the words from the beginning to \u201cdetained\u201d "
        "substitute \u201cWhere a person is detained under section 4(2), the\u201d;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_detained",
            "replacement": "Where a person is detained under section 4(2), the",
        }
    ]


def test_parse_fragment_substitution_handles_passive_range_to_end_repeal_with_ordinal() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1) the words from \u201cto\u201d, where thirdly occurring, "
        "to the end are repealed; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_to_TO_END",
            "replacement": "",
            "occurrence": "3",
            "rule_id": "uk_effect_range_to_end_passive_ordinal_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_passive_range_repeal_with_end_occurrence() -> None:
    subs = parse_fragment_substitution(
        "c in paragraph 6, the words from \u201c, unless\u201d to \u201ccase,\u201d, "
        "where it first occurs, are repealed;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_, unless_TO_case,",
            "replacement": "",
            "end_occurrence": "1",
            "rule_id": "uk_effect_range_independent_end_occurrence_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_repeal_with_pre_predicate_comma() -> None:
    subs = parse_fragment_substitution(
        "a in subsection (1), the words from \u201cwhere\u201d to \u201cBankruptcy\u201d, "
        "are repealed; and"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_where_TO_Bankruptcy",
            "replacement": "",
            "rule_id": "uk_effect_range_repeal_pre_predicate_comma_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_listed_word_and_range_to_end_repeal() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (3)(b) the words\u2014 i \u201cthe first\u201d, and ii from "
        "\u201cmade\u201d to the end, are repealed."
    )

    assert subs == [
        {
            "original": "the first",
            "replacement": "",
            "rule_id": "uk_effect_listed_word_and_range_to_end_repeal_text_patch",
        },
        {
            "original": "TEXT_FROM_made_TO_END",
            "replacement": "",
            "rule_id": "uk_effect_listed_word_and_range_to_end_repeal_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_range_where_ordinal_occurring_substitution() -> None:
    subs = parse_fragment_substitution(
        "b in subsection (1)(a), for the words from \u201can\u201d, where second occurring, "
        "to \u201csurveillance\u201d substitute \u201c the authorisation \u201d ,"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_an_TO_surveillance",
            "replacement": "the authorisation",
            "occurrence": "2",
            "rule_id": "uk_effect_range_occurrence_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_with_independent_end_occurrence() -> None:
    subs = parse_fragment_substitution(
        "for the words from \u201cnotify\u201d, where first occurring, to \u201cGuardian\u201d, "
        "where second occurring, substitute \u201c notify the Public Guardian \u201d"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_notify_TO_Guardian",
            "replacement": "notify the Public Guardian",
            "occurrence": "1",
            "end_occurrence": "2",
            "rule_id": "uk_effect_range_independent_end_occurrence_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_range_unquoted_substitution() -> None:
    subs = parse_fragment_substitution(
        "7 In section 14(5)(a), for the words from \u201cmember\u201d to \u201cand\u201d "
        "substitute constable of the Police Service; and aa another case."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_member_TO_and",
            "replacement": "constable of the Police Service; and aa another case.",
            "rule_id": "uk_effect_range_unquoted_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_bare_range_unquoted_substitution() -> None:
    subs = parse_fragment_substitution(
        "a from \u201care to\u201d to \u201clicence\u201d substitute "
        "(including public charge points) are to the person entitled, by virtue of\u2014 "
        "a a statutory right, b a street works licence, or c where the apparatus "
        "is a public charge point installed in England in pursuance of a street "
        "works permit, the permit, ;"
    )

    assert subs == [
        {
            "original": "TEXT_FROM_are to_TO_licence",
            "replacement": (
                "(including public charge points) are to the person entitled, by virtue of\u2014 "
                "a a statutory right, b a street works licence, or c where the apparatus "
                "is a public charge point installed in England in pursuance of a street "
                "works permit, the permit, ;"
            ),
            "rule_id": "uk_effect_bare_range_unquoted_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_is_replaced_with() -> None:
    subs = parse_fragment_substitution(
        "In subsection (2), the words “Alpha” is replaced with “Beta”."
    )

    assert subs == [{"original": "Alpha", "replacement": "Beta"}]


def test_parse_fragment_substitution_handles_from_beginning_block_substitution() -> None:
    subs = parse_fragment_substitution(
        "2 For the words from the beginning to “the registrar may” substitute— "
        "A1 This section applies where..."
    )
    assert subs == [
        {
            "original": "TEXT_FROM__TO_the registrar may",
            "replacement": "A1 This section applies where...",
            "rule_id": "uk_effect_from_beginning_block_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_proviso_child_substitution() -> None:
    subs = parse_fragment_substitution(
        "For paragraph (ii) of the proviso substitute— "
        "ii an investigation under Part 1 of the 2009 Act..."
    )
    assert subs == [
        {
            "original": "TEXT_PROVISO_CHILD_ii",
            "replacement": "ii an investigation under Part 1 of the 2009 Act...",
            "rule_id": "uk_effect_proviso_child_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_paragraphs_range_substitution() -> None:
    subs = parse_fragment_substitution(
        "for paragraphs (a) and (b) substitute “ , on furnishing the prescribed particulars, ”"
    )
    assert subs == [
        {
            "original": "TEXT_REPLACE_CHILDREN_PARAGRAPH_a_b",
            "replacement": ", on furnishing the prescribed particulars,",
            "rule_id": "uk_effect_paragraphs_range_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_wherever_appearing_substitution() -> None:
    subs = parse_fragment_substitution(
        "2 For “Relevant Authority”, wherever appearing, substitute “appropriate authority”."
    )

    assert subs == [
        {
            "original": "Relevant Authority",
            "replacement": "appropriate authority",
            "rule_id": "uk_effect_wherever_appearing_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_at_each_place_substitution() -> None:
    subs = parse_fragment_substitution(
        "ii for “committee”, at each place, substitute “appropriate tribunal”;"
    )

    assert subs == [
        {
            "original": "committee",
            "replacement": "appropriate tribunal",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_bare_in_each_case_substitution() -> None:
    subs = parse_fragment_substitution(
        "for “House of Lords” in each case substitute “ Supreme Court ” ."
    )

    assert subs == [
        {
            "original": "House of Lords",
            "replacement": " Supreme Court ",
            "rule_id": "uk_effect_all_occurrences_substitution_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_before_anchor_ordinal_appears_insert() -> None:
    subs = parse_fragment_substitution(
        "a before “228”, in the second place it appears, insert “226B or”, and"
    )

    assert subs == [
        {
            "original": "228",
            "replacement": "226B or 228",
            "occurrence": "2",
            "rule_id": "uk_effect_before_quoted_anchor_ordinal_insert_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_unnumbered_ordinal_paragraph_omission() -> None:
    subs = parse_fragment_substitution("c omit the third unnumbered paragraph.")

    assert subs == [
        {
            "original": "TEXT_PARAGRAPH_3",
            "replacement": "",
            "rule_id": "uk_effect_ordinal_paragraph_repeal_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_opening_words_omission() -> None:
    subs = parse_fragment_substitution("a omit the opening words;")

    assert subs == [
        {
            "original": "TEXT_OPENING_WORDS",
            "replacement": "",
            "rule_id": "uk_effect_opening_words_omission_text_patch",
        }
    ]


def test_parse_fragment_substitution_handles_dash_definition_list_omission() -> None:
    subs = parse_fragment_substitution(
        "3 In subsection (2), omit the definitions of— "
        "“the board” “local authority's list” “Poisons Rules”."
    )

    assert subs == [
        {
            "original": "TEXT_DEFINITION_ENTRY_the board",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
        {
            "original": "TEXT_DEFINITION_ENTRY_local authority's list",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
        {
            "original": "TEXT_DEFINITION_ENTRY_Poisons Rules",
            "replacement": "",
            "rule_id": "uk_effect_definition_entry_repeal_text_patch",
        },
    ]


def test_parse_fragment_substitution_handles_parenthetical_range_start_occurrence_omission() -> None:
    subs = parse_fragment_substitution(
        "b in paragraph (c), omit the words from “an authority” "
        "(in the second place where it occurs) to “(joint waste authorities),”."
    )

    assert subs == [
        {
            "original": "TEXT_FROM_an authority_TO_(joint waste authorities),",
            "replacement": "",
            "rule_id": "uk_effect_range_occurrence_repeal_text_patch",
            "occurrence": "2",
        }
    ]
