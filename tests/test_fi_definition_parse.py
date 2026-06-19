"""Unit tests for the definition-entry construction parse (Pilot B).

Covers the DefinitionParse-lite IR, total-token-ownership (no silent drop), the
projection to the production definition-binding key form, and the family-census
classification — on hand-built witness definition sentences/blocks in the
coordinate space the SegmentationGraph produces (the decoded body text). Does NOT
touch the corpus.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.definition_parse import (
    DEFINITION_LANE_CONSTRUCTION_OWNED,
    DEFINITION_LANE_DECLINED,
    ENTRY_MARKER_NOT_IN_TAPE,
    assert_total_ownership,
    definition_key,
    parse_definition_block,
    projection_definition_keys,
)
from lawvm.finland.legal_surface.family_census import classify


# --------------------------------------------------------------------------
# Bare enumerated-block header (chapeau owned; items in following segments)
# --------------------------------------------------------------------------


def test_bare_enumerated_header_owns_chapeau() -> None:
    # The SegmentationGraph splits the definitions-block chapeau sentence from its
    # enumerated items, so the L0 union census feeds the header ALONE. The header
    # is a genuine definition-construction opener — own the chapeau (the
    # ``tarkoitetaan`` cue is no longer silent-unowned) with ZERO entries, instead
    # of declining the whole span.
    for text in ("Tässä asetuksessa tarkoitetaan:", "Tässä laissa tarkoitetaan:"):
        dp = parse_definition_block(text)
        assert dp.kind == "definition_header", text
        assert dp.parser_lane == DEFINITION_LANE_CONSTRUCTION_OWNED, text
        assert dp.entries == ()
        assert dp.chapeau_span == (0, len(text))  # the whole header is owned
        # zero entry keys → the family census is unchanged (empty projection)
        assert projection_definition_keys(dp) == set()
        assert_total_ownership(dp)


# --------------------------------------------------------------------------
# Post-verb inline definition (``Tässä <unit> tarkoitetaan <X-adessive> Y``)
# --------------------------------------------------------------------------


def test_postverb_inline_definition_owned() -> None:
    # The colon-less header idiom: the definiendum FOLLOWS the verb. Production's
    # inline arm requires a PRE-verb definiendum and its enumerated arm requires a
    # colon, so this was silent-unowned. The post-verb arm now owns it.
    text = "Tässä laissa tarkoitetaan kemikaalilla alkuaineita ja niiden yhdisteitä."
    dp = parse_definition_block(text)
    assert dp.kind == "single_sentence"
    assert dp.parser_lane == DEFINITION_LANE_CONSTRUCTION_OWNED
    assert len(dp.entries) == 1
    assert dp.entries[0].term == "kemikaalilla"
    assert dp.entries[0].scope == "statute"
    assert_total_ownership(dp)


def test_postverb_inline_multiword_definiendum_owned() -> None:
    text = (
        "Tässä laissa tarkoitetaan terveydelle vaarallisella kemikaalilla "
        "kemikaalia, joka voi aiheuttaa haittaa."
    )
    dp = parse_definition_block(text)
    assert len(dp.entries) == 1
    assert dp.entries[0].term == "terveydelle vaarallisella kemikaalilla"
    assert_total_ownership(dp)


def test_postverb_inline_definiens_act_cite_binds_target() -> None:
    text = (
        "Tässä laissa tarkoitetaan sivutuoteasetuksella asetusta "
        "(EY) N:o 1069/2009."
    )
    dp = parse_definition_block(text)
    assert len(dp.entries) == 1
    assert dp.entries[0].term == "sivutuoteasetuksella"
    assert dp.entries[0].target_ref == "1069/2009"
    assert_total_ownership(dp)


def test_postverb_referential_idiom_declines() -> None:
    # A relative pronoun in the pre-verb clause (``…, joilla … tarkoitetaan …``)
    # is the REFERENTIAL idiom ("which is referred to …"); the post-verb material
    # is the referent list, NOT a definiendum. Decline — no swept-clause binding.
    text = (
        "Sulkukanavan kautta saavat kulkea alukset, joilla tässä "
        "järjestyssäännössä tarkoitetaan matkustajalaivoja, jotka kulkevat "
        "omalla koneistolla."
    )
    dp = parse_definition_block(text)
    assert dp.parser_lane == DEFINITION_LANE_DECLINED
    assert dp.entries == ()
    assert_total_ownership(dp)


def test_postverb_definiens_first_partitive_declines() -> None:
    # ``tarkoitetaan <partitive-object>`` with NO adessive definiendum head (the
    # definiendum lives in a prior segment) → no entry (fail-loud, no fabrication).
    text = "tarkoitetaan tunnistamistietojen hankkimista viestistä."
    dp = parse_definition_block(text)
    assert dp.parser_lane == DEFINITION_LANE_DECLINED
    assert dp.entries == ()
    assert_total_ownership(dp)


# --------------------------------------------------------------------------
# Enumerated definition block (chapeau + entries)
# --------------------------------------------------------------------------


def test_enumerated_block_parses_entries_and_is_owned() -> None:
    text = (
        "Tässä laissa tarkoitetaan:\n"
        "sivutuotteella kuollutta eläintä;\n"
        "jätteellä jätelaissa tarkoitettua ainetta."
    )
    dp = parse_definition_block(text)
    assert dp.kind == "definition_block"
    assert dp.parser_lane == DEFINITION_LANE_CONSTRUCTION_OWNED
    assert dp.chapeau_cue == "tarkoitetaan"
    terms = sorted(e.term for e in dp.entries)
    assert "sivutuotteella" in terms
    assert "jätteellä" in terms
    # every enumerated entry inherits the header scope + marker-not-in-tape role
    assert all(e.scope == "statute" for e in dp.entries)
    assert all(e.entry_marker_role == ENTRY_MARKER_NOT_IN_TAPE for e in dp.entries)
    assert_total_ownership(dp)


def test_enumerated_block_scope_from_header_unit() -> None:
    text = "Tässä luvussa tarkoitetaan:\nvälineellä laitetta tai konetta."
    dp = parse_definition_block(text)
    assert dp.kind == "definition_block"
    assert dp.entries
    assert all(e.scope == "chapter" for e in dp.entries)
    assert_total_ownership(dp)


def test_block_with_numbered_markers_owned() -> None:
    # the N) markers ARE absent from the decoded <p> tape in production, but the
    # parser must tolerate them when present (they fall in the item-leader run).
    text = (
        "Tässä laissa tarkoitetaan:\n"
        "1) hakijalla luonnollista henkilöä;\n"
        "2) viranomaisella valtion virastoa."
    )
    dp = parse_definition_block(text)
    terms = sorted(e.term for e in dp.entries)
    assert "hakijalla" in terms
    assert "viranomaisella" in terms
    assert_total_ownership(dp)


# --------------------------------------------------------------------------
# Single-sentence inline definition
# --------------------------------------------------------------------------


def test_single_sentence_inline_definition() -> None:
    text = "Tässä laissa sivutuotteella tarkoitetaan kuollutta eläintä."
    dp = parse_definition_block(text)
    assert dp.kind == "single_sentence"
    assert dp.parser_lane == DEFINITION_LANE_CONSTRUCTION_OWNED
    assert len(dp.entries) == 1
    e = dp.entries[0]
    assert e.term == "sivutuotteella"  # leading scope locative trimmed
    assert e.entry_marker_role == ""  # not enumerated
    assert_total_ownership(dp)


def test_definiens_with_act_reference_binds_target() -> None:
    text = (
        "Tässä laissa sivutuoteasetuksella tarkoitetaan asetusta "
        "(EY) N:o 1069/2009 annettua säädöstä."
    )
    dp = parse_definition_block(text)
    assert len(dp.entries) == 1
    # the shared act-id recognizer lifts the EU act cite in the definiens
    assert dp.entries[0].target_ref == "1069/2009"
    assert_total_ownership(dp)


# --------------------------------------------------------------------------
# Decline / no-fabrication
# --------------------------------------------------------------------------


def test_referential_tarkoitetaan_declines() -> None:
    # the REFERENTIAL idiom ("which is referred to in subsection 2") binds nothing
    text = "Edellytyksistä, joita 2 momentissa tarkoitetaan, säädetään erikseen."
    dp = parse_definition_block(text)
    assert dp.parser_lane == DEFINITION_LANE_DECLINED
    assert dp.entries == ()
    # the whole span is one explicit residual (no silent drop, no fabrication)
    assert len(dp.residuals) == 1
    assert_total_ownership(dp)


def test_non_definition_text_declines_as_residue() -> None:
    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
    dp = parse_definition_block(text)
    assert dp.parser_lane == DEFINITION_LANE_DECLINED
    assert dp.entries == ()
    assert dp.residuals[0].reason == "not_definition_bearing"
    assert_total_ownership(dp)


# --------------------------------------------------------------------------
# Projection + key form + census classification
# --------------------------------------------------------------------------


def test_projection_keys_normalize_term_and_carry_scope() -> None:
    text = "Tässä laissa tarkoitetaan:\nhakijalla luonnollista henkilöä."
    dp = parse_definition_block(text)
    keys = projection_definition_keys(dp)
    assert "hakijalla|statute" in keys


def test_definition_key_form() -> None:
    assert definition_key("Hakijalla", "statute", None) == "hakijalla|statute"
    assert definition_key("X:llä", "chapter", None) == "x:llä|chapter"
    assert (
        definition_key("term", "statute", "1069/2009") == "term|statute|1069/2009"
    )
    # whitespace-normalized multi-word definiendum
    assert (
        definition_key("multi  word  term", "statute", None)
        == "multi word term|statute"
    )


def test_census_classification_against_oracle_on_witness_block() -> None:
    text = (
        "Tässä laissa tarkoitetaan:\n"
        "sivutuotteella kuollutta eläintä;\n"
        "jätteellä jätelaissa tarkoitettua ainetta."
    )
    from lawvm.finland.legal_surface.definition_census import (
        _definition_oracle_keys_for_span,
    )

    dp = parse_definition_block(text)
    proj = projection_definition_keys(dp)
    oracle = _definition_oracle_keys_for_span(text)
    # the construction mirrors production's enumerated-block segmentation, so a
    # clean witness block is in parity (no miss).
    assert oracle - proj == set()
    bucket = classify(proj, oracle, dp.parser_lane == DEFINITION_LANE_DECLINED)
    assert bucket in ("match", "superset")
