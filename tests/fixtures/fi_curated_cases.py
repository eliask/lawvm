#!/usr/bin/env python3
"""Phase 1.4 — Curated PEG case data with feature annotations.

Generated from Phase 1.2 corpus analysis (18,969 johtolause texts,
greedy set cover + focused per-feature cases).

Feature coverage (25 tags):
    All 25 tags covered. part_ref tested via targeted grammar case
    (absent from post-2000 corpus; all-years corpus run pending).
    Former xfail (law-level range insert) now passes in peg3.

Usage:
    Imported as fixture data by `tests/test_peg_curated.py` and related tests.
"""
from __future__ import annotations

import argparse
import sys

from lawvm.finland.johtolause.api import parse_clause, derive_features

# ---------------------------------------------------------------------------
# Test cases
# Each case: name, text, expected op codes, features exercised, xfail flag
# xfail=True: known failure documented in peg_failure_inventory.md (CAT-F)
# ---------------------------------------------------------------------------

CURATED_CASES = [
    # ------------------------------------------------------------------
    # Verb detection
    # ------------------------------------------------------------------
    {
        "name": "verb_muuttaa basic",
        "text": "muutetaan 12 §",
        "expected": ["M P 12"],
        "features": {"verb_muuttaa", "section_ref"},
    },
    {
        "name": "verb_kumota basic",
        "text": "kumotaan 7 §",
        "expected": ["K P 7"],
        "features": {"verb_kumota", "section_ref"},
    },
    {
        "name": "verb_lisata basic",
        "text": "lisätään 8 §:ään uusi 3 momentti",
        "expected": ["L P 8 3"],
        "features": {"verb_lisata", "insertion_pykala_ill"},
    },
    {
        "name": "verb_siirtaa basic",
        "text": "siirretään 3 §",
        "expected": ["S P 3"],
        "features": {"verb_siirtaa", "section_ref"},
    },
    # ------------------------------------------------------------------
    # Section references
    # ------------------------------------------------------------------
    {
        "name": "section_ref conj list",
        "text": "muutetaan 3, 5 ja 7 §",
        "expected": ["M P 3", "M P 5", "M P 7"],
        "features": {"verb_muuttaa", "section_ref", "conj_target_list"},
    },
    {
        "name": "section_ref range expansion",
        "text": "muutetaan 21–23 §",
        "expected": ["M P 21", "M P 22", "M P 23"],
        "features": {"verb_muuttaa", "section_ref", "range_expansion"},
    },
    {
        "name": "section_ref letter suffix",
        "text": "muutetaan 5 a §",
        "expected": ["M P 5a"],
        "features": {"verb_muuttaa", "section_ref", "letter_suffix"},
    },
    {
        "name": "section_ref sub_ref_momentti",
        "text": "muutetaan 5 §:n 2 momentti",
        "expected": ["M P 5 2"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti"},
    },
    {
        "name": "section_ref archaic a separator between subsection targets",
        "text": "muutetaan 1 §:n 1 momentti a 7 §:n 1 momentti",
        "expected": ["M P 1 1", "M P 7 1"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "conj_target_list", "archaic_a_conj",
        },
    },
    {
        "name": "section_ref numeric genitive surface form canonicalizes target",
        "text": "muutetaan 1:n § 2 momentti",
        "expected": ["M P 1 2"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti"},
    },
    {
        "name": "section_ref sub_ref_kohta (momentin kohta)",
        "text": "muutetaan 5 §:n 1 momentin 3 kohta",
        "expected": ["M P 5 1 3"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_kohta"},
    },
    {
        "name": "section_ref same-mom multi-kohta (ja)",
        "text": "muutetaan 70 §:n 2 momentin 1 ja 3 kohta",
        "expected": ["M P 70 2 1", "M P 70 2 3"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_kohta", "conj_target_list"},
    },
    {
        "name": "section_ref alternating-depth mom+kohta (sekä)",
        "text": "muutetaan 70 §:n 2 momentin 1 ja 3 kohdan sekä 4 momentin 1 kohdan",
        "expected": ["M P 70 2 1", "M P 70 2 3", "M P 70 4 1"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_kohta", "conj_target_list"},
    },
    {
        "name": "section_ref mom kohta range expansion",
        "text": "muutetaan 70 §:n 2 momentin 1\u20133 kohta",
        "expected": ["M P 70 2 1", "M P 70 2 2", "M P 70 2 3"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_kohta", "range_expansion"},
    },
    {
        "name": "section_ref cross-mom kohta (ja)",
        "text": "muutetaan 70 §:n 2 momentin 1 kohdan ja 3 momentin 2 kohdan",
        "expected": ["M P 70 2 1", "M P 70 3 2"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_kohta", "conj_target_list"},
    },
    {
        "name": "section_ref sub_ref_otsikko",
        "text": "muutetaan 6 §:n otsikko",
        "expected": ["M P 6 o"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_otsikko"},
    },
    {
        "name": "section_ref sub_ref_johd",
        "text": "muutetaan 15 §:n johdantokappale",
        "expected": ["M P 15 j"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_johd"},
    },
    {
        "name": "section_ref johdantokappale survives trailing provenance citation",
        "text": (
            "muutetaan maatalouden tukien toimeenpanosta annetun lain (192/2013) "
            "3 §:n 1 momentin johdantokappale, sellaisena kuin se on laissa "
            "1356/2014, seuraavasti:"
        ),
        "expected": ["M P 3 1 j"],
        "features": {"verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_johd"},
    },
    {
        "name": "section_ref johdantokappale sanamuoto keeps later refs alive",
        "text": (
            "muutetaan 3 §:n 1 momentin johdantokappaleen ruotsinkielinen sanamuoto, "
            "3 §:n 2 momentti, 4 §, 6 a §:n 1 momentti, 9 §:n johdantokappaleen "
            "ruotsinkielinen sanamuoto, 9 §:n 4 kohta, 9 a ja 10 §, "
            "11 §:n 1 momentin ruotsinkielinen sanamuoto ja 11 a § sekä liite"
        ),
        "expected": [
            "M P 3 1 j", "M P 3 2", "M P 4", "M P 6a 1",
            "M P 9 j", "M P 9 1 4", "M P 9a", "M P 10",
            "M P 11 1", "M P 11a", "M A ",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti", "sub_ref_johd",
            "sub_ref_kohta", "appendix_ref", "conj_target_list", "letter_suffix",
        },
    },
    {
        "name": "section_ref johdantolause keeps trailing section refs alive",
        "text": "muutetaan 48 §:n 1 momentin johdantolause ja 5 momentti, 49 ja 50 §, 51 §:n 3 momentti sekä 53 §",
        "expected": ["M P 48 1 j", "M P 48 5", "M P 49", "M P 50", "M P 51 3", "M P 53"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "sub_ref_johd", "conj_target_list",
        },
    },
    {
        "name": "section_ref comma-continued intro target keeps sibling items and later sections",
        "text": "muutetaan 48 §:n 1 momentin johdantokappale, 2 ja 4 kohta sekä 5 momentti, 49 a §:n 2 momentti, 50 §, 51 §:n 3 momentti ja 53 §",  # noqa: E501
        "expected": ["M P 48 1 j", "M P 48 1 2", "M P 48 1 4", "M P 48 5", "M P 49a 2", "M P 50", "M P 51 3", "M P 53"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "sub_ref_johd", "sub_ref_kohta", "conj_target_list", "letter_suffix",
        },
    },
    {
        "name": "section_ref alakohta qualifier keeps later sibling items alive",
        "text": (
            "muutetaan 2 §:n 1―3, 5―7, 9 ja 10 kohta, 12 kohdan a alakohta, "
            "13, 14, 17 ja 20―24 kohta sekä 4 §:n 1 momentin 1 kohta"
        ),
        "expected": [
            "M P 2 1 1", "M P 2 1 2", "M P 2 1 3", "M P 2 1 5", "M P 2 1 6",
            "M P 2 1 7", "M P 2 1 9", "M P 2 1 10", "M P 2 1 12", "M P 2 1 13",
            "M P 2 1 14", "M P 2 1 17", "M P 2 1 20", "M P 2 1 21",
            "M P 2 1 22", "M P 2 1 23", "M P 2 1 24", "M P 4 1 1",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_kohta",
            "sub_ref_momentti", "conj_target_list", "range_expansion",
        },
    },
    {
        "name": "section_ref initial alakohta qualifier keeps later sibling item alive",
        "text": "muutetaan 2 §:n 1 kohdan h alakohta ja 10 kohta",
        "expected": ["M P 2 1 1", "M P 2 1 10"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_kohta", "conj_target_list",
        },
    },
    {
        "name": "section_ref language qualifier keeps later sections alive",
        "text": (
            "muutetaan 14 §:n otsikon ruotsinkielinen sanamuoto, "
            "14 §:n 1 ja 3 momentti, 14 c § sekä 19 ja 20 §"
        ),
        "expected": [
            "M P 14 o", "M P 14 1", "M P 14 3", "M P 14c", "M P 19", "M P 20",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_otsikko",
            "sub_ref_momentti", "conj_target_list", "letter_suffix",
        },
    },
    {
        "name": "edella oleva valiotsikko sanamuoto keeps later section list alive",
        "text": (
            "muutetaan 11 §:n edellä olevan väliotsikon ruotsinkielinen sanamuoto, "
            "16 ja 16 b §, 16 c §:n 1—3 momentti, 17 §:n 2 momentti, 19 ja 20 §, "
            "22 ja 24 §, 27, 29—31 ja 31 a §, 32 §:n 1 ja 3 momentti sekä 34 ja 35 a §"
        ),
        "expected": [
            "M P 11 o", "M P 16", "M P 16b", "M P 16c 1", "M P 16c 2", "M P 16c 3",
            "M P 17 2", "M P 19", "M P 20", "M P 22", "M P 24", "M P 27",
            "M P 29", "M P 30", "M P 31", "M P 31a", "M P 32 1", "M P 32 3",
            "M P 34", "M P 35a",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_otsikko", "sub_ref_momentti",
            "conj_target_list", "letter_suffix", "range_expansion",
        },
    },
    {
        "name": "anaphoric valiotsikko sanamuoto keeps later section refs alive",
        "text": (
            "muutetaan 26 § ja sen edellä olevan väliotsikon ruotsinkielinen sanamuoto, "
            "27, 29—31 ja 31 a §, 32 §:n 1 ja 3 momentti sekä 34 ja 35 a §"
        ),
        "expected": [
            "M P 26", "M P 26 o", "M P 27", "M P 29", "M P 30", "M P 31", "M P 31a",
            "M P 32 1", "M P 32 3", "M P 34", "M P 35a",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti", "conj_target_list",
            "letter_suffix", "range_expansion", "valiotsikko_heading_target",
        },
    },
    {
        "name": "pykalan edella oleva valiotsikko after subsection keeps later refs alive",
        "text": (
            "muutetaan 19 §:n 1 momentti sekä pykälän edellä olevan väliotsikon "
            "ruotsinkielinen sanamuoto, 20 § ja sen edellä oleva väliotsikko, "
            "21 ja 24 §"
        ),
        "expected": [
            "M P 19 1", "M P 19 o", "M P 20", "M P 20 o",
            "M P 21", "M P 24",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "conj_target_list", "valiotsikko_heading_target",
        },
    },
    {
        "name": "item language qualifier reaches following verb group",
        "text": (
            "muutetaan 23 §:n 8 ja 10 kohdan ruotsinkielinen sanamuoto, "
            "lisätään 2 §:ään uusi 25-35 kohta"
        ),
        "expected": [
            "M P 23 1 8", "M P 23 1 10",
            "L P 2 1 25", "L P 2 1 26", "L P 2 1 27", "L P 2 1 28",
            "L P 2 1 29", "L P 2 1 30", "L P 2 1 31", "L P 2 1 32",
            "L P 2 1 33", "L P 2 1 34", "L P 2 1 35",
        ],
        "features": {
            "verb_muuttaa", "verb_lisata", "section_ref", "sub_ref_kohta",
            "insertion_pykala_ill", "multi_verb_group", "conj_target_list",
            "range_expansion",
        },
    },
    {
        "name": "appendix provenance with internal verb still reaches next verb group",
        "text": (
            "muutetaan 3 §:n otsikko, 7 ja 8 § sekä liite 1, sellaisina kuin "
            "niistä ovat 8 § ja liite 1 asetuksessa 740/2017, ja lisätään "
            "3 §:ään uusi 2 momentti seuraavasti:"
        ),
        "expected": ["M P 3 o", "M P 7", "M P 8", "M A 1", "L P 3 2"],
        "features": {
            "verb_muuttaa", "verb_lisata", "section_ref", "appendix_ref",
            "sub_ref_otsikko", "insertion_pykala_ill", "multi_verb_group",
            "provenance_skip", "conj_target_list",
        },
    },
    {
        "name": "section_ref trailing bare-pykala sub_ref scopes to last section",
        "text": "muutetaan 32, 34 ja 38 § 5 ja 10 momentti",
        "expected": ["M P 32", "M P 34", "M P 38 5", "M P 38 10"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "conj_target_list", "trailing_sub_ref_scope",
        },
    },
    {
        "name": "section_ref skips alakohta tail and continues list",
        "text": "muutetaan 6 §:n 1 momentin 3 kohdan d ja e alakohta ja 4 kohta, 7 §:n 1 ja 3 momentti",
        "expected": ["M P 6 1 3", "M P 6 1 4", "M P 7 1", "M P 7 3"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "sub_ref_kohta", "conj_target_list",
        },
    },
    {
        "name": "section_ref letter kohta compound token continues list",
        "text": "muutetaan 9 §:n 1 momentin a-kohta, 10 §:n 1 ja 2 momentti",
        "expected": ["M P 9 1 a", "M P 10 1", "M P 10 2"],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref_momentti",
            "sub_ref_kohta", "conj_target_list",
        },
    },
    {
        "name": "insertion target list keeps jolloin clause",
        "text": "lisätään 7 §:ään uusi 4 ja 5 momentti, jolloin nykyinen 4-8 momentti siirtyvät 6-10 momentiksi, lakiin uusi 17 a §",  # noqa: E501
        "expected": ["S P 7 4", "S P 7 5", "S P 7 6", "S P 7 7", "S P 7 8", "L P 7 4", "L P 7 5", "L P 17a"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "insertion_law_level",
            "conj_target_list", "letter_suffix", "verb_siirtya",
        },
    },
    {
        "name": "insertion momentti reinstatement clause with letter item continues list",
        "text": "lisätään 9 §:n 1 momenttiin siitä lailla 1363/1992 kumotun b kohdan tilalle uusi b kohta, 10 §:ään uusi 7 momentti",  # noqa: E501
        "expected": ["L P 9 1 b", "L P 10 7"],
        "features": {
            "verb_lisata", "insertion_momentti_ill", "insertion_pykala_ill",
            "conj_target_list",
        },
    },
    {
        "name": "insertion chapter reinstatement clause keeps chapter illative context",
        "text": "muutetaan 10 luvun otsikko ja lisätään 10 lukuun siitä lailla 361/1999 kumotun 14 §:n tilalle uusi 14 § seuraavasti:",  # noqa: E501
        "expected": ["M L 10 o", "L P L:10 14"],
        "features": {
            "verb_muuttaa", "verb_lisata", "chapter_ref",
            "multi_verb_group", "chapter_ctx_propagation",
            "insertion_reinstatement", "statute_name_filter",
        },
    },
    {
        "name": "part otsikko keeps later chapter and insertion groups alive",
        "text": (
            "muutetaan IV osan otsikko, 12 luvun 3 ja 4 § ja lisätään "
            "19 luvun 3 §:ään uusi 3 momentti, 19 lukuun uusi 4 a ja 5 a §, "
            "19 lukuun siitä lailla 1078/2017 kumotun 6 §:n tilalle uusi 6 §, "
            "19 lukuun uusi 6 a §"
        ),
        "expected": [
            "M O IV o", "M P L:12 3", "M P L:12 4", "L P L:19 3 3",
            "L P L:19 4a", "L P L:19 5a", "L P L:19 6", "L P L:19 6a",
        ],
        "features": {
            "verb_muuttaa", "verb_lisata", "part_ref", "section_ref",
            "multi_verb_group", "conj_target_list", "insertion_pykala_ill",
            "insertion_chapter_illative", "chapter_ctx_propagation",
        },
    },
    {
        "name": "insertion skips chained provenance continuation",
        "text": "lisätään 1 §:ään, sellaisena kuin se on osittain muutettuna 9 päivänä toukokuuta 1986 annetulla lailla (333/86) ja mainitulla 27 päivänä maaliskuuta 1991 annetulla lailla, uusi 8 momentti",  # noqa: E501
        "expected": ["L P 1 8"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "provenance_skip",
            "split_citation",
        },
    },
    {
        "name": "insertion chained same-section uusi tail survives jolloin",
        "text": "lisätään 18 §:ään uusi 2 momentti sekä uusi 6-8 momentti, jolloin nykyinen 6-10 momentti siirtyvät 9-13 momentiksi, sekä lakiin uusi 25 a §",  # noqa: E501
        "expected": ["S P 18 6", "S P 18 7", "S P 18 8", "S P 18 9", "S P 18 10", "L P 18 2", "L P 18 6", "L P 18 7", "L P 18 8", "L P 25a"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "insertion_law_level",
            "conj_target_list", "letter_suffix", "range_expansion", "verb_siirtya",
        },
    },
    # ------------------------------------------------------------------
    # Chapter references
    # ------------------------------------------------------------------
    {
        "name": "chapter_ref repeal",
        "text": "kumotaan 3 luku",
        "expected": ["K L 3"],
        "features": {"verb_kumota", "chapter_ref"},
    },
    {
        "name": "chapter_ref otsikko",
        "text": "muutetaan 5 luvun otsikko",
        "expected": ["M L 5 o"],
        "features": {"verb_muuttaa", "chapter_ref", "sub_ref_otsikko"},
    },
    {
        "name": "chapter_ctx_propagation",
        "text": "muutetaan 3 luvun 12 §:n 2 momentti",
        "expected": ["M P L:3 12 2"],
        "features": {"verb_muuttaa", "section_ref", "chapter_ctx_propagation", "sub_ref_momentti"},
    },
    {
        "name": "chapter_heading_resets_stale_section_context",
        "text": "muutetaan 1 luvun 2 §, 3 luvun otsikko, 3 §, 4 § ja 5 a §",
        "expected": ["M P L:1 2", "M L 3 o", "M P L:3 3", "M P L:3 4", "M P L:3 5a"],
        "features": {"verb_muuttaa", "section_ref", "chapter_ref", "sub_ref_otsikko", "conj_target_list", "chapter_ctx_propagation"},
    },
    {
        "name": "chapter_repeal_no_propagation",
        "text": "kumotaan 3 luku, muutetaan 5 §",
        "expected": ["K L 3", "M P 5"],
        "features": {
            "verb_kumota", "verb_muuttaa",
            "chapter_ref", "section_ref",
            "chapter_repeal_no_propagation", "multi_verb_group", "conj_target_list",
        },
    },
    # ------------------------------------------------------------------
    # Insertions
    # ------------------------------------------------------------------
    {
        "name": "insertion_pykala_ill new momentti",
        "text": "lisätään 8 §:ään uusi 3 momentti",
        "expected": ["L P 8 3"],
        "features": {"verb_lisata", "insertion_pykala_ill"},
    },
    {
        "name": "insertion_pykala_ill new kohta (letter)",
        "text": "lisätään 3 §:ään uusi 8 a kohta",
        "expected": ["L P 3 1 8a"],
        "features": {"verb_lisata", "insertion_pykala_ill", "letter_suffix", "sub_ref_kohta"},
    },
    {
        "name": "insertion_momentti_ill new kohta",
        "text": "lisätään 3 §:n 1 momenttiin uusi 5 kohta",
        "expected": ["L P 3 1 5"],
        "features": {"verb_lisata", "insertion_momentti_ill"},
    },
    {
        "name": "insertion_momentti_ill multi-kohta",
        "text": "lisätään 3 §:n 1 momenttiin uusi 10 ja 11 kohta",
        "expected": ["L P 3 1 10", "L P 3 1 11"],
        "features": {"verb_lisata", "insertion_momentti_ill", "conj_target_list"},
    },
    {
        "name": "insertion_pykala_ill multi-momentti",
        "text": "lisätään 8 §:ään uusi 3 ja 4 momentti",
        "expected": ["L P 8 3", "L P 8 4"],
        "features": {"verb_lisata", "insertion_pykala_ill", "conj_target_list"},
    },
    {
        "name": "insertion_law_level new section",
        "text": "lisätään lakiin uusi 5 a §",
        "expected": ["L P 5a"],
        "features": {"verb_lisata", "insertion_law_level", "letter_suffix"},
    },
    {
        "name": "insertion_law_level spaced suffix range",
        "text": "lisätään lakiin uusi 9 b–9 d §",
        "expected": ["L P 9b", "L P 9c", "L P 9d"],
        "features": {
            "verb_lisata", "insertion_law_level", "letter_suffix", "range_expansion",
        },
    },
    {
        "name": "insertion_law_level new chapter",
        "text": "lisätään lakiin uusi 3 luku",
        "expected": ["L L 3"],
        "features": {"verb_lisata", "insertion_law_level", "chapter_ref"},
    },
    {
        "name": "insertion anaphoric pykala stays on latest section target",
        "text": (
            "lisätään 3 §:ään, sellaisena kuin se on muutettuna mainituilla 20 päivänä "
            "heinäkuuta 1992 ja 10 päivänä syyskuuta 1993 annetuilla laeilla, uusi 3 "
            "momentti, 4 §:n 1 momenttiin, sellaisena kuin se on viimeksi mainitussa "
            "laissa, uusi 10 kohta, 9 §:ään uusi 2 momentti, lakiin uusi 4 a luku, "
            "49 §:n 1 momenttiin, sellaisena kuin se on osittain muutettuna mainitulla "
            "20 päivänä heinäkuuta 1992 annetulla lailla, uusi 7 kohta ja pykälään "
            "uusi 2 momentti, jolloin nykyinen 2 ja 3 momentti siirtyvät 3 ja 4 "
            "momentiksi, sekä 59 a §:ään, sellaisena kuin se on muutettuna 3 päivänä "
            "joulukuuta 1993 ja 5 päivänä tammikuuta 1994 annetuilla laeilla "
            "(1092/93 ja 25/94), uusi 4 momentti, seuraavasti:"
        ),
        "expected": [
            "S P 49 2", "S P 49 3",
            "L P 3 3", "L P 4 1 10", "L P 9 2", "L L 4a",
            "L P 49 1 7", "L P 49 2", "L P 59a 4",
        ],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "insertion_momentti_ill",
            "insertion_law_level", "insertion_anaphoric_pykala",
            "chapter_ref", "letter_suffix", "conj_target_list", "verb_siirtya",
        },
    },
    {
        "name": "insertion_law_level asetus multi",
        "text": "lisätään asetukseen uusi 2 a, 8 a ja 13 a §",
        "expected": ["L P 2a", "L P 8a", "L P 13a"],
        "features": {"verb_lisata", "insertion_law_level", "letter_suffix", "conj_target_list"},
    },
    # ------------------------------------------------------------------
    # Inherited-suffix insert ranges (uuden … §:n) — Pattern 1B gap fix
    # GEN form of § (§:n) appears in real corpus as a stylistic variant
    # when the uuden phrase is embedded in a larger clause.  PEG3 Pattern C
    # used to reject §:n (GEN) at law-level; the fix emits INSERT ops when
    # no momentti/kohta follows the §:n.
    # ------------------------------------------------------------------
    {
        "name": "insertion_law_level uuden single gen pykala",
        "text": "lisätään lakiin uuden 14 a §:n",
        "expected": ["L P 14a"],
        "features": {"verb_lisata", "insertion_law_level", "letter_suffix",
                     "uuden_gen_pykala"},
    },
    {
        "name": "insertion_law_level uuden comma list gen pykala",
        "text": "lisätään lakiin uuden 14 a, 14 b ja 14 c §:n",
        "expected": ["L P 14a", "L P 14b", "L P 14c"],
        "features": {"verb_lisata", "insertion_law_level", "letter_suffix",
                     "conj_target_list", "uuden_gen_pykala"},
    },
    {
        "name": "insertion_law_level uuden range gen pykala",
        "text": "lisätään lakiin uuden 14b–14d §:n",
        "expected": ["L P 14b", "L P 14c", "L P 14d"],
        "features": {"verb_lisata", "insertion_law_level", "letter_suffix",
                     "range_expansion", "uuden_gen_pykala"},
    },
    # ------------------------------------------------------------------
    # Compound chapter + section inserts (Pattern 3A)
    # "lakiin uusi N luku ja M §" — chapter and sections in one clause.
    # PEG3 handles these via _target_list chaining: Pattern C parses the
    # chapter op, then the separator loop parses the section list as a
    # second target. No dedicated compound rule is needed.
    # ------------------------------------------------------------------
    {
        "name": "compound_chapter_section ja list",
        "text": "lisätään lakiin uusi 2 luku ja 15, 16 ja 17 §",
        "expected": ["L L 2", "L P 15", "L P 16", "L P 17"],
        "features": {
            "verb_lisata", "insertion_law_level", "chapter_ref",
            "compound_chapter_section", "conj_target_list",
        },
    },
    {
        "name": "compound_chapter_section seka uusi range",
        "text": "lisätään lakiin uusi 6 a luku sekä uusi 82 a ja 83 a §",
        "expected": ["L L 6a", "L P 82a", "L P 83a"],
        "features": {
            "verb_lisata", "insertion_law_level", "chapter_ref",
            "compound_chapter_section", "letter_suffix", "conj_target_list",
        },
    },
    {
        "name": "compound_chapter_section range expansion",
        "text": "lisätään lakiin uusi 2 luku ja 15–17 §",
        "expected": ["L L 2", "L P 15", "L P 16", "L P 17"],
        "features": {
            "verb_lisata", "insertion_law_level", "chapter_ref",
            "compound_chapter_section", "range_expansion",
        },
    },
    # ------------------------------------------------------------------
    # Other structural refs
    # ------------------------------------------------------------------
    {
        "name": "appendix_ref",
        "text": "muutetaan 1 § ja liite",
        "expected": ["M P 1", "M A "],
        "features": {"verb_muuttaa", "section_ref", "appendix_ref", "conj_target_list"},
    },
    {
        "name": "nimike_ref",
        "text": "muutetaan nimike ja 1 §",
        "expected": ["M N ", "M P 1"],
        "features": {"verb_muuttaa", "nimike_ref", "section_ref", "conj_target_list"},
    },
    {
        "name": "part_ref",
        "text": "muutetaan 1 osa",
        "expected": ["M O 1"],
        "features": {"verb_muuttaa", "part_ref"},
        # Not found in post-2000 corpus; this is a targeted grammar test
        "_corpus_absent": True,
    },
    {
        "name": "part_ref roman numerals",
        "text": "muutetaan III ja V osa",
        "expected": ["M O III", "M O V"],
        "features": {"verb_muuttaa", "part_ref", "conj_target_list"},
        "_corpus_absent": True,
    },
    {
        "name": "old mixed section and roman numeral part refs",
        "text": "kumotaan 55 §, III ja V osa",
        "expected": ["K P 55", "K O III", "K O V"],
        "features": {"verb_kumota", "section_ref", "part_ref", "conj_target_list"},
        "_corpus_absent": True,
    },
    {
        "name": "old roman numeral part refs with trailing provenance keep next verb group alive",
        "text": "kumotaan 13 päivänä kesäkuuta 1929 annetun avioliittolain (234/29) 55§, III ja V osa niihin myöhemmin tehtyine muutoksineen, muutetaan I osa",
        "expected": ["K P 55", "K O III", "K O V", "M O I"],
        "features": {"verb_kumota", "verb_muuttaa", "section_ref", "part_ref", "conj_target_list", "provenance_skip", "multi_verb_group"},
        "_corpus_absent": True,
    },
    # ------------------------------------------------------------------
    # Noise handling
    # ------------------------------------------------------------------
    {
        "name": "provenance_skip (sellaisena kuin)",
        "text": "kumotaan 29 §, sellaisena kuin se on laissa 732/2008",
        "expected": ["K P 29"],
        "features": {"verb_kumota", "section_ref", "provenance_skip"},
    },
    {
        "name": "statute_name_filter compact citation",
        "text": "muutetaan omaishoidon tuesta annetun lain (937/2005) 5 §",
        "expected": ["M P 5"],
        "features": {"verb_muuttaa", "section_ref", "statute_name_filter"},
    },
    {
        "name": "statute_name_filter skip_statute_name fallback",
        # 'muutettu' before '85 b §' has no citation — handled by skip_statute_name
        "text": "siirretään muutettu 85 b § 9 lukuun",
        "expected": ["S P 85b"],
        "features": {"verb_siirtaa", "section_ref", "letter_suffix", "statute_name_filter"},
    },
    # ------------------------------------------------------------------
    # Multi-verb groups
    # ------------------------------------------------------------------
    {
        "name": "multi_verb_group 2 verbs",
        "text": "kumotaan 3 § sekä muutetaan 5 §",
        "expected": ["K P 3", "M P 5"],
        "features": {"verb_kumota", "verb_muuttaa", "section_ref", "multi_verb_group"},
    },
    {
        "name": "multi_verb_group 3 verbs",
        "text": "kumotaan 3 §, muutetaan 5 § sekä lisätään 7 §:ään uusi 2 momentti",
        "expected": ["K P 3", "M P 5", "L P 7 2"],
        "features": {
            "verb_kumota", "verb_muuttaa", "verb_lisata",
            "section_ref", "insertion_pykala_ill",
            "multi_verb_group", "conj_target_list",
        },
    },
    {
        "name": "multi_verb_group explicit section insert does not inherit chapter",
        "text": "muutetaan 3 luvun 12 § ja lisätään 13 §:ään uusi 2 momentti",
        "expected": ["M P L:3 12", "L P 13 2"],
        "features": {
            "verb_muuttaa", "verb_lisata", "section_ref",
            "insertion_pykala_ill", "multi_verb_group",
            "chapter_ctx_propagation",
        },
    },
    {
        "name": "multi_verb_group anaphoric lukuun insert keeps chapter",
        "text": "muutetaan 3 luvun 12 § ja lisätään lukuun uusi 13 a §",
        "expected": ["M P L:3 12", "L P L:3 13a"],
        "features": {
            "verb_muuttaa", "verb_lisata", "section_ref",
            "insertion_chapter_level", "multi_verb_group",
            "chapter_ctx_propagation", "letter_suffix",
        },
    },
    # ------------------------------------------------------------------
    # Split citation (filter-stage feature)
    # ------------------------------------------------------------------
    {
        "name": "split_citation + nimike_ref + appendix_ref",
        # Citation split by whitespace: "( 1182/2009 )"
        "text": "muutetaan veroasetuksen ( 1182/2009 ) nimike, 1 ja 3 § sekä liite",
        "expected": ["M N ", "M P 1", "M P 3", "M A "],
        "features": {
            "verb_muuttaa", "split_citation", "nimike_ref", "section_ref",
            "appendix_ref", "conj_target_list",
        },
    },
    # ------------------------------------------------------------------
    # Corpus integration tests (from Phase 1.3 greedy set cover)
    # Real-world complex johtolause texts — validated by corpus runner
    # ------------------------------------------------------------------
    {
        "name": "corpus_2019_1405 insertion_momentti_ill letter_suffix",
        "text": (
            "lisätään\n                         "
            "työaikalakiin (872/2019) uusi 8 a § ja 34 §:n 2 momenttiin "
            "uusi 2 a kohta seuraavasti:"
        ),
        "expected": ["L P 8a", "L P 34 2 2a"],
        "features": {
            "verb_lisata", "section_ref", "letter_suffix",
            "conj_target_list", "insertion_momentti_ill",
            "split_citation", "statute_name_filter",
        },
    },
    {
        "name": "corpus_2020_575 verb_siirtaa multi_verb provenance",
        "text": (
            "muutetaan\n                         "
            "maksupalvelulain (290/2010) 85 b ja 85 c §, sellaisena kuin ne ovat "
            "laissa 898/2017,\n                        siirretään\n                         "
            "muutettu 85 b § 9 lukuun ja lisätään\n                         "
            "lakiin uusi 85 d § seuraavasti:"
        ),
        # Same-label move is now modeled by retargeting the prior replace op
        # instead of emitting a standalone S-op.
        "expected": ["M P L:9 85b", "M P 85c", "L P 85d"],
        "features": {
            "verb_muuttaa", "verb_siirtaa",
            "section_ref", "letter_suffix",
            "multi_verb_group", "provenance_skip",
            "split_citation", "statute_name_filter",
            "insertion_lakiin_uusi",
        },
    },
    {
        "name": "inline_move_tail_doc_ill_lakiin_lisattavaan",
        # 2019/52 → 2014/917: inline move tail contains DOC:ILL "lakiin" and
        # WORD "lisättävään" before the destination number ("29 a lukuun").
        # Previously _inline_move_clause_tail_destination() failed to consume
        # the DOC:ILL token, so _number_list() saw "lakiin" instead of "29"
        # and the tail fell through, causing the parser to break on the VERB
        # and drop all subsequent targets (271a, 272, 325).
        "text": (
            "muutetaan tietoyhteiskuntakaaren (917/2014) 250 §, "
            "joka samalla siirretään lakiin lisättävään 29 a lukuun, "
            "271 a §, 272 §:n 1 momentin johdantokappale ja 325 §:n 2 momentti seuraavasti:"
        ),
        "expected": [
            "M P L:29a 250",
            "M P 271a",
            "M P 272 1 j",
            "M P 325 2",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "letter_suffix",
            "inline_move_tail", "doc_ill_before_dest_number",
            "statute_name_filter", "sub_ref",
        },
    },
    {
        "name": "comma_separated_gen_scope_only_last",
        # 2022/1182 → 2007/1438: "22, 24 §:n 1 momentti" — comma before
        # genitive §:n means only the LAST number (24) inherits the sub-ref.
        # Leading numbers (22, 44, 51) are whole-section ops.
        # Previously the GEN case distributed sub-refs to ALL numbers.
        "text": (
            "muutetaan asevelvollisuuslain (1438/2007) 22, 24 §:n 1 momentti, "
            "44, 51, 81 §:n 2 momentti ja 109 §:n 1 momentti seuraavasti:"
        ),
        "expected": [
            "M P 22",
            "M P 24 1",
            "M P 44",
            "M P 51",
            "M P 81 2",
            "M P 109 1",
        ],
        "features": {
            "verb_muuttaa", "section_ref", "sub_ref",
            "comma_gen_scope", "statute_name_filter",
        },
    },
    # ------------------------------------------------------------------
    # Adversarial / known failure cases (CAT-F from peg_failure_inventory.md)
    # ------------------------------------------------------------------
    {
        "name": "law-level suffix-section range insert",
        # PEG3's own tokenizer handles range expressions correctly.
        "text": (
            "lisätään lakiin uusi 1 a—1 c, 2 a, 3 a, 3 b, 11 a—11 d ja 12 a § "
            "sekä 14 §:ään uusi 3 momentti seuraavasti:"
        ),
        "expected": [
            "L P 1a", "L P 1b", "L P 1c", "L P 2a", "L P 3a", "L P 3b",
            "L P 11a", "L P 11b", "L P 11c", "L P 11d", "L P 12a", "L P 14 3",
        ],
        "features": {
            "verb_lisata", "insertion_law_level", "insertion_pykala_ill",
            "letter_suffix", "range_expansion", "conj_target_list",
        },
    },
    # ------------------------------------------------------------------
    # Complex multi-verb johtolause (from golden verification journal)
    # ------------------------------------------------------------------
    {
        "name": "complex_2025_414_saamelaiskäräjälaki (kumotaan+muutetaan+lisätään)",
        # 2025/414 for 1995/974: 82 operations across 3 verbs.
        # Previously produced 0 ops — _is_structural_num failed on range dashes,
        # skip_statute_name consumed the structural references, and
        # Historical bug: older extraction treated single letters too narrowly.
        "text": (
            "kumotaan saamelaiskäräjistä annetun lain (974/1995) "
            "18 e–18 i, 23 a, 26 b, 26 d, 27 a, 42 a ja 42 b §, "
            "muutetaan "
            "1–4, 4 a, 5–16, 18, 18 a, 18 d, 19–25, 25 a, 26, 26 a, "
            "27–31, 31 a, 31 c, 31 h, 32, 34, 35, 38–40 ja 40 c §, "
            "5 luvun otsikko, 41 §, 6 luvun otsikko sekä 42 ja 43 §, "
            "lisätään "
            "lakiin uusi 9 a, 9 b, 17 a, 17 b, 25 b, 41 a–41 m, "
            "43 a ja 43 b § sekä liite seuraavasti:"
        ),
        "expected": [
            # kumotaan (11)
            "K P 18e", "K P 18f", "K P 18g", "K P 18h", "K P 18i",
            "K P 23a", "K P 26b", "K P 26d", "K P 27a", "K P 42a", "K P 42b",
            # muutetaan (50)
            "M P 1", "M P 2", "M P 3", "M P 4", "M P 4a",
            "M P 5", "M P 6", "M P 7", "M P 8", "M P 9", "M P 10",
            "M P 11", "M P 12", "M P 13", "M P 14", "M P 15", "M P 16",
            "M P 18", "M P 18a", "M P 18d",
            "M P 19", "M P 20", "M P 21", "M P 22", "M P 23", "M P 24", "M P 25",
            "M P 25a", "M P 26", "M P 26a",
            "M P 27", "M P 28", "M P 29", "M P 30", "M P 31",
            "M P 31a", "M P 31c", "M P 31h",
            "M P 32", "M P 34", "M P 35",
            "M P 38", "M P 39", "M P 40", "M P 40c",
            "M L 5 o", "M P L:5 41", "M L 6 o", "M P L:6 42", "M P L:6 43",
            # lisätään (21)
            "L P 9a", "L P 9b", "L P 17a", "L P 17b", "L P 25b",
            "L P 41a", "L P 41b", "L P 41c", "L P 41d", "L P 41e",
            "L P 41f", "L P 41g", "L P 41h", "L P 41i", "L P 41j",
            "L P 41k", "L P 41l", "L P 41m",
            "L P 43a", "L P 43b", "L A ",
        ],
        "features": {
            "verb_kumota", "verb_muuttaa", "verb_lisata",
            "section_ref", "chapter_ref", "appendix_ref",
            "letter_suffix", "range_expansion", "conj_target_list",
            "multi_verb_group", "insertion_law_level",
            "split_citation", "chapter_ctx_propagation",
        },
    },
    {
        "name": "complex_väliaikainen_2021_701 (lisätään väliaikaisesti)",
        # 2021/701 for 2016/1227: temporary insertion with range.
        # Previously produced only 3 ops (missing 16a-16f range);
        # skip_statute_name consumed "väliaikaisesti uusi" and range tokens.
        "text": (
            "lisätään tartuntatautilakiin (1227/2016) "
            "väliaikaisesti uusi 16 a–16 g, 87 a ja 89 a § seuraavasti:"
        ),
        "expected": [
            "L P 16a", "L P 16b", "L P 16c", "L P 16d",
            "L P 16e", "L P 16f", "L P 16g",
            "L P 87a", "L P 89a",
        ],
        "features": {
            "verb_lisata", "section_ref", "letter_suffix",
            "range_expansion", "conj_target_list", "split_citation",
        },
    },
    {
        "name": "multi_verb_group law-level temporary suffix range insert",
        "text": (
            "muutetaan väliaikaisesti saatavien perinnästä annetun lain "
            "(513/1999) 7 §, sellaisena kuin se on laissa 31/2013, sekä "
            "lisätään lakiin väliaikaisesti uusi 3 b–3 e § seuraavasti:"
        ),
        "expected": ["M P 7", "L P 3b", "L P 3c", "L P 3d", "L P 3e"],
        "features": {
            "verb_muuttaa", "verb_lisata", "multi_verb_group",
            "insertion_law_level", "letter_suffix", "range_expansion",
            "conj_target_list", "split_citation",
        },
    },
    # ==================================================================
    # Renumbering: "§:n numero N:ksi"
    # ==================================================================
    {
        "name": "renumber single section",
        "text": "muutetaan 1 §:n numero 3:ksi",
        "expected": ["M P 1"],
        "features": {"renumber", "section_ref"},
    },
    {
        "name": "renumber with backref momentti",
        "text": "muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti",
        "expected": ["M P 2", "M P 2 1"],
        "features": {"renumber", "backref_singular", "sub_ref"},
    },
    {
        "name": "renumber with backref whole section",
        "text": "muutetaan 11 §:n numero 13:ksi ja mainittu pykälä",
        "expected": ["M P 11", "M P 11"],
        "features": {"renumber", "backref_singular"},
    },
    {
        "name": "renumber with backref otsikko",
        "text": "muutetaan 8 §:n numero 10:ksi ja mainitun pykälän otsikko",
        "expected": ["M P 8", "M P 8 o"],
        "features": {"renumber", "backref_singular", "otsikko"},
    },
    {
        "name": "renumber with backref complex sub-refs",
        "text": (
            "muutetaan 4 §:n numero 6:ksi ja mainitun pykälän "
            "1 momentin 2 ja 3 kohta, 2 momentin 1 ja 2 kohta ja 3 momentti"
        ),
        "expected": [
            "M P 4", "M P 4 1 2", "M P 4 1 3",
            "M P 4 2 1", "M P 4 2 2", "M P 4 3",
        ],
        "features": {"renumber", "backref_singular", "sub_ref", "conj_target_list"},
    },
    {
        "name": "renumber plural backref otsikot",
        "text": "muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien otsikot",
        "expected": ["M P 5", "M P 6", "M P 5 o", "M P 6 o"],
        "features": {"renumber", "backref_plural", "otsikko"},
    },
    {
        "name": "renumber comma-separated backref",
        "text": (
            "muutetaan 7 §:n numero 9:ksi, mainitun pykälän otsikko, "
            "1 momentin 2 kohta ja 2 momentti"
        ),
        "expected": ["M P 7", "M P 7 o", "M P 7 1 2", "M P 7 2"],
        "features": {"renumber", "backref_singular", "otsikko", "sub_ref"},
    },
    {
        "name": "renumber chain across sections",
        "text": (
            "muutetaan 1 §:n numero 3:ksi, 2 §:n numero 4:ksi "
            "ja mainitun pykälän 1 momentti, 3 §:n numero 5:ksi "
            "ja mainitun pykälän 3 momentti"
        ),
        "expected": [
            "M P 1", "M P 2", "M P 2 1",
            "M P 3", "M P 3 3",
        ],
        "features": {"renumber", "backref_singular", "conj_target_list"},
    },
    # ==================================================================
    # Provenance stripping: ensure structural tokens survive
    # ==================================================================
    {
        "name": "provenance strips but preserves trailing target",
        "text": (
            "muutetaan sellaisena kuin se on laissa 100/2021 3 § ja 5 §"
        ),
        "expected": ["M P 3", "M P 5"],
        "features": {"provenance_strip", "section_ref"},
    },
    {
        "name": "provenance after part ref preserves relative move continuation",
        "text": (
            "muutetaan I osa, sellaisena kuin se on siihen myöhemmin tehtyine muutoksineen, "
            "30 ja 31§, jotka samalla siirretään I osaan"
        ),
        "expected": ["M O I", "M P O:I 30", "M P O:I 31"],
        "features": {"verb_muuttaa", "part_ref", "section_ref", "provenance_skip"},
    },
    {
        "name": "provenance with mainitun lain (not backref)",
        "text": (
            "kumotaan sellaisena kuin se on muutettuna laissa 100/2021 "
            "ja mainitun lain 5 § 3 momentti"
        ),
        # "mainitun lain 5 §" is provenance continuation — the 5 § refers
        # to section 5 OF THE OTHER LAW, not a target of this amendment.
        # This should produce NO ops (entire clause is provenance + no targets).
        "expected": [],
        "features": {"provenance_strip", "backref_provenance"},
    },
    {
        "name": "provenance with backref pykälän survives",
        "text": (
            "muutetaan 7 §, sellaisena kuin se on laissa 200/2022, "
            "ja mainitun pykälän 2 momentti"
        ),
        # "mainitun pykälän" = structural backref, NOT provenance
        "expected": ["M P 7", "M P 7 2"],
        "features": {"provenance_strip", "backref_singular", "sub_ref"},
    },
    {
        "name": "sellaisena kuin with ovat does not leak",
        "text": (
            "muutetaan 1 ja 3 §, sellaisina kuin niistä ovat "
            "1 § laissa 50/2020 ja 3 § laissa 60/2021, sekä "
            "lisätään lakiin uusi 5 §"
        ),
        "expected": ["M P 1", "M P 3", "L P 5"],
        "features": {"provenance_strip", "multi_verb_group", "ovat_internal"},
    },
    {
        "name": "provenance_ovat_enumeration_does_not_leak_targets",
        "text": (
            "muutetaan kaivoslain (621/2011) 3 §, 32 §:n 1 ja 3 momentti, "
            "34 §:n 3 momentin 2 kohta ja 34 §:n 5 momentti, "
            "sellaisina kuin ne ovat, "
            "3 § laissa 530/2014, 32 §:n 1 ja 3 momentti sekä "
            "34 §:n 5 momentti laissa 578/2019 sekä "
            "34:n § 3 momentin 2 kohta laissa 259/2017, seuraavasti:"
        ),
        "expected": ["M P 3", "M P 32 1", "M P 32 3", "M P 34 3 2", "M P 34 5"],
        "features": {"provenance_skip", "ovat_internal"},
    },
    # ==================================================================
    # Back-reference forms: all declensions
    # ==================================================================
    {
        "name": "backref mainitun pykälän genitive",
        "text": "muutetaan 3 §:n numero 5:ksi ja mainitun pykälän otsikko ja 1 momentti",
        "expected": ["M P 3", "M P 3 o", "M P 3 1"],
        "features": {"renumber", "backref_singular", "otsikko", "sub_ref"},
    },
    {
        "name": "backref mainittu pykälä nominative",
        "text": "muutetaan 11 §:n numero 13:ksi ja mainittu pykälä",
        "expected": ["M P 11", "M P 11"],
        "features": {"renumber", "backref_singular"},
    },
    {
        "name": "backref mainittujen pykälien genitive plural",
        "text": "muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien 1 momentti",
        "expected": ["M P 5", "M P 6", "M P 5 1", "M P 6 1"],
        "features": {"renumber", "backref_plural", "sub_ref"},
    },
    # ==================================================================
    # Heading/intro targets
    # ==================================================================
    {
        "name": "johdantokappale after section",
        "text": "muutetaan 7 §:n 1 momentin johdantokappale",
        "expected": ["M P 7 1 j"],
        "features": {"section_ref", "johdantokappale"},
    },
    {
        "name": "otsikot plural after section list",
        "text": "muutetaan 1 ja 2 §:n otsikko",
        "expected": ["M P 1 o", "M P 2 o"],
        "features": {"section_ref", "otsikko", "conj_target_list"},
    },
    # ==================================================================
    # Part context (xfail — grammar gap)
    # ==================================================================
    {
        "name": "part_ctx section in part",
        "text": "muutetaan II osan 1 luvun 3 §",
        "expected": ["M P O:II L:1 3"],
        "features": {"part_ctx", "section_ref"},
    },
    {
        "name": "part_ctx chapter renumber",
        "text": "muutetaan II osan 1 luvun numero 2:ksi",
        "expected": ["M L O:II 1"],
        "features": {"part_ctx", "renumber"},
    },
    {
        "name": "part_ctx chapter range renumber",
        "text": "muutetaan II osan 5-7 luvun numero 6-8:ksi",
        "expected": ["M L O:II 5", "M L O:II 6", "M L O:II 7"],
        "features": {"part_ctx", "renumber"},
    },
    {
        "name": "part_ctx otsikko",
        "text": "muutetaan VI osan otsikko",
        "expected": ["M O VI o"],
        "features": {"part_ctx", "otsikko"},
    },
    {
        "name": "part_ctx whole part",
        "text": "muutetaan II osa",
        "expected": ["M O II"],
        "features": {"part_ctx"},
    },
    # ==================================================================
    # Valiotsikko heading targets: "sen edellä oleva väliotsikko"
    # ==================================================================
    {
        "name": "valiotsikko heading simple: section ja sen valiotsikko",
        "text": "muutetaan 5 § ja sen edellä oleva väliotsikko",
        "expected": ["M P 5", "M P 5 o"],
        "features": {"valiotsikko_heading_target", "section_ref"},
    },
    {
        "name": "valiotsikko heading chain: section, valiotsikko, more sections",
        "text": "muutetaan 3 § sekä sen edellä olevan väliotsikon sanamuoto ja 7 §",
        "expected": ["M P 3", "M P 3 o", "M P 7"],
        "features": {"valiotsikko_heading_target", "section_ref", "conj_target_list"},
    },
    {
        "name": "valiotsikko heading pykalan: subsection + pykalan valiotsikko",
        "text": "muutetaan 10 §:n 2 momentti sekä pykälän edellä olevan väliotsikon sanamuoto",
        "expected": ["M P 10 2", "M P 10 o"],
        "features": {"valiotsikko_heading_target", "sub_ref_momentti"},
    },
    {
        "name": "jolloin move consequence preserves following targets",
        "text": (
            "muutetaan 5 §:n 1 momentti, jolloin nykyinen 2 momentti siirtyy "
            "3 momentiksi, ja 8 §"
        ),
        "expected": ["S P 5 2", "M P 5 1", "M P 8"],
        "features": {"jolloin_move", "verb_siirtya", "section_ref", "sub_ref_momentti", "conj_target_list"},
    },
    {
        "name": "jolloin section renumber: single section with letter suffix destination",
        "text": (
            "lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi, "
            "sekä muutetaan 14 §"
        ),
        "expected": ["S P 10", "L P 10", "M P 14"],
        "features": {"jolloin_section_renumber", "verb_lisata", "section_ref"},
    },
    {
        "name": "jolloin section renumber: simple numeric destination",
        "text": (
            "lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi"
        ),
        "expected": ["S P 5", "L P 5"],
        "features": {"jolloin_section_renumber", "verb_lisata", "section_ref"},
    },
    # ------------------------------------------------------------------
    # PEG3 Fix: section-level reinstatement preamble (N §:n tilalle)
    # and anaphoric bare 'uusi N momentti' after jolloin clauses.
    # These tests cover patterns exposed by the provenance-clause gap
    # analysis (Function 2 / PEG3_FALLBACK_GAP_ANALYSIS.md).
    # ------------------------------------------------------------------
    {
        "name": "provenance_pykala_gen_tilalle_does_not_block_continuation",
        "text": (
            "lisätään 13 §:n tilalle uusi 13 §, "
            "15 §:ään uusi 4 momentti"
        ),
        "expected": ["L P 13", "L P 15 4"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "section_reinstatement",
        },
    },
    {
        "name": "sellaisena_kuin_provenance_subsection_insert_with_reinstatement_preamble",
        "text": (
            "lisätään lakiin uusi 1 a §, "
            "13 §:n tilalle uusi 13 §, "
            "15 §:ään, sellaisena kuin se on mainitussa laissa 1529/2001, uusi 4 momentti, "
            "lakiin uusi 20 a § sekä "
            "29 §:ään uusi 2 momentti"
        ),
        "expected": [
            "L P 1a",
            "L P 13",
            "L P 15 4",
            "L P 20a",
            "L P 29 2",
        ],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "insertion_law_level",
            "provenance_skip", "section_reinstatement",
        },
    },
    {
        "name": "anaphoric_bare_uusi_momentti_after_jolloin_clause",
        "text": (
            "lisätään 26 §:ään, sellaisena kuin se on osaksi laissa 1428/2011, "
            "uusi 3 momentti, jolloin muutettu 3 momentti siirtyy 4 momentiksi, "
            "ja uusi 5 momentti"
        ),
        "expected": ["S P 26 3", "L P 26 3", "L P 26 5"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "provenance_skip",
            "jolloin_move", "verb_siirtya", "anaphoric_uusi_momentti",
        },
    },
    # ------------------------------------------------------------------
    # REINST_SPAN: tag-not-delete for reinstatement preambles.
    # Verifies that REINST_SPAN tokens emitted by strip_reinstatement
    # are correctly skipped by the grammar at all relevant TILALLE sites.
    # ------------------------------------------------------------------
    {
        "name": "reinstatement_tag_section_level_pykala_gen_tilalle",
        "text": "lisätään 5 §:n tilalle uusi 5 §",
        "expected": ["L P 5"],
        "features": {
            "verb_lisata", "section_ref", "section_reinstatement",
        },
    },
    {
        "name": "reinstatement_tag_kumotun_preamble_with_continuation",
        "text": (
            "lisätään 3 lukuun siitä lailla 200/2010 kumotun 8 §:n tilalle "
            "uusi 8 §, 4 lukuun uusi 9 a §"
        ),
        "expected": ["L P L:3 8", "L P L:4 9a"],
        "features": {
            "verb_lisata", "insertion_chapter_illative",
            "insertion_reinstatement", "conj_target_list",
            "chapter_ctx_propagation",
        },
    },
    {
        "name": "reinstatement_tag_momentti_level_with_letter_item",
        "text": (
            "lisätään 12 §:n 2 momenttiin siitä lailla 500/2015 kumotun "
            "c kohdan tilalle uusi c kohta"
        ),
        "expected": ["L P 12 2 c"],
        "features": {
            "verb_lisata", "insertion_momentti_ill",
        },
    },
    # ------------------------------------------------------------------
    # CITATION_SPAN: tag-not-delete for statute name + citation spans.
    # Verifies that CITATION_SPAN tokens emitted by strip_statute_citations
    # are correctly skipped by the grammar at all relevant sites.
    # ------------------------------------------------------------------
    {
        "name": "citation_span_verb_to_target_direct",
        # "muutetaan rikoslain (39/1889) 3 §" — citation between VERB and target.
        # CITATION_SPAN replaces "rikoslain (39/1889)"; grammar skips it in _target.
        "text": "muutetaan rikoslain (39/1889) 3 §",
        "expected": ["M P 3"],
        "features": {
            "verb_muuttaa", "section_ref", "citation_span",
        },
    },
    {
        "name": "citation_span_pykala_ill_provenance_uusi",
        # "lisätään 26 §:ään, sellaisena kuin se on osaksi laissa 1428/2011, uusi 3 momentti"
        # — citation inside §:ään insertion provenance clause.
        # CITATION_SPAN lands between §:ään comma and UUSI; Pattern A skips it.
        "text": (
            "lisätään 26 §:ään, sellaisena kuin se on osaksi laissa 1428/2011, "
            "uusi 3 momentti"
        ),
        "expected": ["L P 26 3"],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "provenance_skip",
            "citation_span",
        },
    },
    {
        "name": "citation_span_verb_target_list_naista_provenance",
        # Regression test for strip_statute_names eating the target list when
        # CITATION_SPAN appears right after VERB.  Before the fix, strip_statute_names
        # entered statute-name-scan mode at CITATION_SPAN and consumed the whole
        # target list (4§2mom, 5§1,2,4mom) up to the WORD "näistä", replacing it
        # with STATUTE_NAME_SPAN.  This left only 5§4mom visible to the parser.
        #
        # Source: 1993/1689 amendment 1995/32:
        # "muutetaan yritystuesta ... annetun valtioneuvoston päätöksen (1689/93)
        #  4 §:n 2 momentin sekä 5 §:n 1, 2 ja 4 momentin, näistä 5 §:n 4 momentti
        #  sellaisena kuin se on 24 päivänä marraskuuta 1994 annetussa
        #  valtioneuvoston päätöksessä (1017/94), seuraavasti:"
        #
        # The duplicate 5§4mom comes from "näistä 5 §:n 4 momentti" being re-parsed
        # as a target (same as pre-tag-not-delete behaviour; dedup handles it).
        "text": (
            "muutetaan yritystuesta 30 päivänä joulukuuta 1993 annetun "
            "valtioneuvoston päätöksen (1689/93) 4 §:n 2 momentin sekä 5 §:n 1, "
            "2 ja 4 momentin, näistä 5 §:n 4 momentti sellaisena kuin se on "
            "24 päivänä marraskuuta 1994 annetussa valtioneuvoston päätöksessä "
            "(1017/94), seuraavasti:"
        ),
        "expected": ["M P 4 2", "M P 5 1", "M P 5 2", "M P 5 4", "M P 5 4"],
        "features": {
            "verb_muuttaa", "section_ref", "subsection_ref", "conj_target_list",
            "citation_span", "provenance_skip",
        },
    },
    # ------------------------------------------------------------------
    # sekä DOC:ILL continuation inside insert verb group
    # ------------------------------------------------------------------
    {
        "name": "seka_docill_continuation_numbered_chapter_heading",
        # "sekä lisätään asetukseen uusi 13 a ... 16 b §, 17 §:n edelle uusi 2 a
        # luvun otsikko, sekä asetukseen uusi 18 a § ja 2 b luku"
        #
        # Two bugs fixed:
        # 1. Heading scan in _target_list did not recognise "uusi N [a] luvun otsikko"
        #    (only "uusi väliotsikko" and "uusi luvun otsikko" were handled).
        # 2. After heading skip, a leading CONJ (sekä) before the next DOC:ILL was
        #    consumed as a separator so Pattern C could pick up "asetukseen uusi ...".
        "text": (
            "sekä lisätään asetukseen uusi 13 a, 13 b, 13 c, 16 a ja 16 b §, "
            "17 §:n edelle uusi 2 a luvun otsikko, sekä asetukseen uusi 18 a § "
            "ja 2 b luku"
        ),
        "expected": ["L P 13a", "L P 13b", "L P 13c", "L P 16a", "L P 16b",
                     "L P 17 o",  # heading insertion before §17
                     "L P 18a", "L L 2b"],
        "features": {
            "verb_lisata", "section_ref", "chapter_ref", "insertion_docill",
            "heading_skip", "seka_docill_continuation",
        },
    },
    {
        "name": "seka_docill_continuation_provenance_span_residue",
        # "sekä lisätään lakiin uusi 25 a ja 25 b §, 38 §:n edelle uusi väliotsikko,
        # lakiin uusi 38 a ja 46 a § ja viimeksi mainitun edelle uusi väliotsikko
        # sekä lakiin uusi 46 b §, 8 a luku sekä 85 b, 85 c ja 86 b-86 d §"
        #
        # Fix: after provenance filter tags "ja viimeksi mainitun edelle" as
        # PROVENANCE_SPAN and leaves "uusi väliotsikko" as residue, _target_list's
        # _sep() consumed the span but returned None (next was UUSI, not a separator).
        # _skip_heading_residue() now consumes the bare "uusi väliotsikko" residue
        # so the loop can reach the following "sekä lakiin uusi 46 b §, 8 a luku
        # sekä 85 b, 85 c ja 86 b-86 d §".
        "text": (
            "sekä lisätään lakiin uusi 25 a ja 25 b §, 38 §:n edelle uusi väliotsikko, "
            "lakiin uusi 38 a ja 46 a § ja viimeksi mainitun edelle uusi väliotsikko "
            "sekä lakiin uusi 46 b §, 8 a luku sekä 85 b, 85 c ja 86 b-86 d §"
        ),
        "expected": ["L P 25a", "L P 25b",
                     "L P 38 o",  # heading insertion before §38
                     "L P 38a", "L P 46a",
                     "L P 46b", "L L 8a",
                     "L P 85b", "L P 85c", "L P 86b", "L P 86c", "L P 86d"],
        "features": {
            "verb_lisata", "section_ref", "chapter_ref", "insertion_docill",
            "heading_skip", "provenance_skip", "seka_docill_continuation",
        },
    },
    # ------------------------------------------------------------------
    # Chapter scope reset after DOC:ILL (asetukseen/lakiin)
    # ------------------------------------------------------------------
    {
        # Bug fix: chapter scope from "7 c lukuun" must not carry forward
        # past "asetukseen" (DOC:ILL), which returns to statute level.
        # Before fix: 118/2 incorrectly got L:7c scope.
        "name": "chapter_scope_resets_after_asetukseen",
        "text": (
            "lisätään asetuksen 7 c lukuun uusi 55 a § ja 55 b §, "
            "asetukseen uusi 115 a § ja 118 §:ään uusi 2 momentti"
        ),
        "expected": [
            "L P L:7c 55a",   # chapter:7c (within the chapter scope)
            "L P L:7c 55b",   # chapter:7c (within the chapter scope)
            "L P 115a",       # no chapter (asetukseen = statute level)
            "L P 118 2",      # no chapter (after DOC:ILL reset)
        ],
        "features": {
            "verb_lisata", "insertion_pykala_ill", "insertion_docill",
            "chapter_ctx_propagation",
        },
    },
    {
        # Same pattern with "lakiin" — chapter scope resets at statute level.
        "name": "chapter_scope_resets_after_lakiin",
        "text": (
            "lisätään lain 3 lukuun uusi 10 a §, lakiin uusi 15 a §"
        ),
        "expected": [
            "L P L:3 10a",    # chapter:3
            "L P 15a",        # no chapter (lakiin = statute level)
        ],
        "features": {
            "verb_lisata", "insertion_docill", "chapter_ctx_propagation",
        },
    },
    # ------------------------------------------------------------------
    # Heading insertion before section: "N §:n edelle uusi luvun otsikko"
    # ------------------------------------------------------------------
    {
        # The pattern "53 §:n edelle uusi luvun otsikko" produces a
        # heading insertion op for section 53 (the "edelle" anchor).
        "name": "edelle_luvun_otsikko_produces_heading_op",
        "text": (
            "lisätään lakiin uusi 53 a § ja 53 §:n edelle uusi luvun otsikko"
        ),
        "expected": [
            "L P 53a",        # the section insert is parsed
            "L P 53 o",       # heading insertion before §53
        ],
        "features": {
            "verb_lisata", "insertion_docill", "heading_skip",
        },
    },
]


def run_curated_tests(nlp=None, verbose: bool = False) -> bool:
    """Run curated test suite. Returns True if all non-xfail cases pass.

    The nlp parameter is accepted for backward compatibility but ignored.
    peg3 has no NLP dependency.
    """
    print(f"\n{'=' * 70}")
    print("PEG CURATED TEST SUITE — PHASE 1.4")
    print(f"{'=' * 70}\n")

    passed = 0
    failed = 0
    xfailed = 0  # expected failures
    xpassed = 0  # unexpected passes (xfail that passed)
    feature_hits: set = set()

    for tc in CURATED_CASES:
        tc_text = str(tc["text"])
        is_xfail = tc.get("xfail", False)
        try:
            result = parse_clause(tc_text)
            ops = result.parsed_ops
            features = derive_features(tc_text, ops)
            actual = [op.code() for op in ops]
            feature_hits.update(features)
        except Exception as e:
            actual = [f"ERROR: {e}"]
            features = set()

        expected = tc["expected"]
        ok = actual == expected
        tc_features: set[str] = set(tc.get("features", set()))  # ty: ignore[invalid-argument-type]

        if is_xfail:
            if ok:
                xpassed += 1
                status = "XPASS"
            else:
                xfailed += 1
                status = "xfail"
        else:
            if ok:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"

        show = verbose or status in ("FAIL", "XPASS")
        if show or status == "PASS":
            print(f"  [{status}] {tc['name']}")
            if verbose:
                print(f"         Features: {sorted(tc_features)}")
            if not ok:
                print(f"         Input:    {tc_text[:100]}")
                print(f"         Expected: {expected}")
                print(f"         Actual:   {actual}")
            print()

    # Feature coverage summary
    all_expected: set[str] = set()
    for tc in CURATED_CASES:
        all_expected.update(set(tc.get("features", set())))  # ty: ignore[invalid-argument-type]
    covered = feature_hits & all_expected
    uncovered = all_expected - feature_hits

    print(f"{'=' * 70}")
    print(f"Results: {passed}/{passed + failed} non-xfail passed, "
          f"{xfailed} expected failures, {xpassed} unexpected passes")
    print(f"Feature coverage: {len(covered)}/{len(all_expected)} tags hit")
    if uncovered:
        print(f"Uncovered tags:  {sorted(uncovered)}")
    print()
    return failed == 0


def main():
    parser = argparse.ArgumentParser(description="Phase 1.4 curated PEG test suite")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show features and input for all cases")
    args = parser.parse_args()

    ok = run_curated_tests(verbose=args.verbose)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
