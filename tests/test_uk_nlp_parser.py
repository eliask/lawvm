from __future__ import annotations

from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution


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


def test_parse_fragment_substitution_handles_is_replaced_with() -> None:
    subs = parse_fragment_substitution(
        "In subsection (2), the words “Alpha” is replaced with “Beta”."
    )

    assert subs == [{"original": "Alpha", "replacement": "Beta"}]
