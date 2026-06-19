"""Tests for the EU/treaty boundary SCAFFOLD (synthetic only; no network).

These prove SHAPE alignment, not cross-jurisdiction closure. Two claims:

  1. The synthetic EUR-Lex-shaped fragment parses into the SAME
     ``SourceSurfaceBundle`` / ``SourceSurfaceUnit`` shape the FI pipeline uses.
  2. ``celex_to_entity_id`` mints an id byte-identical to the corpus graph's EU
     frontier-node convention — verified against the ACTUAL minting path the FI
     side uses (``mint_entity_node_id`` + the ``celex:<CELEX>`` canonical id from
     ``finland/references/resolve.py``).
"""
from __future__ import annotations

import pytest

from lawvm.core.legal_surface_assembler import mint_entity_node_id
from lawvm.core.legal_surface_lens import SourceSurfaceBundle, SourceSurfaceUnit
from lawvm.eu_lex import (
    EuActDocument,
    build_eu_surface_bundle,
    celex_to_canonical_id,
    celex_to_entity_id,
    is_well_formed_celex,
    parse_eu_act_fragment,
)
from lawvm.eu_lex.bundle import SYNTHETIC_EU_ACT_XML, decode_eu_body_text

# GDPR — the real CELEX the FI registry seeds (see eu_nickname.py). Used to prove
# the id we mint matches what the FI side would carry for that exact act.
_GDPR_CELEX = "32016R0679"
_SYNTHETIC_CELEX = "32099R0001"


# ── celex_to_entity_id alignment with the FI frontier node ───────────────────


def test_celex_canonical_id_matches_fi_resolver_form() -> None:
    # finland/references/resolve.py mints the canonical id as f"celex:{celex}".
    # The raw CELEX is used verbatim (no case folding).
    assert celex_to_canonical_id(_GDPR_CELEX) == f"celex:{_GDPR_CELEX}"


def test_celex_entity_id_matches_corpus_graph_minting() -> None:
    # The corpus graph mints an EU work entity via mint_entity_node_id over the
    # canonical id. Verify our helper is byte-identical to that exact path —
    # this is the join that makes a future EU node land on the FI frontier node.
    expected = mint_entity_node_id(f"celex:{_GDPR_CELEX}")
    assert celex_to_entity_id(_GDPR_CELEX) == expected
    assert celex_to_entity_id(_GDPR_CELEX) == "entity:celex:32016R0679"


def test_celex_entity_id_preserves_case() -> None:
    # FI side does no case normalization; neither must we, or ids diverge.
    cid = celex_to_entity_id(_GDPR_CELEX)
    assert "R" in cid and "r0679" not in cid


def test_is_well_formed_celex_accepts_real_shapes() -> None:
    assert is_well_formed_celex("32016R0679")  # regulation
    assert is_well_formed_celex("32010L0075")  # directive
    assert is_well_formed_celex("31992L0043")  # 1992 directive


def test_is_well_formed_celex_rejects_garbage() -> None:
    assert not is_well_formed_celex("")
    assert not is_well_formed_celex("GDPR")
    assert not is_well_formed_celex("2016R0679")  # missing sector digit
    assert not is_well_formed_celex("32016r0679")  # lowercase descriptor


def test_celex_helpers_refuse_malformed_input() -> None:
    # Fail loud: never mint a non-aligning id from malformed input.
    with pytest.raises(ValueError):
        celex_to_canonical_id("not-a-celex")
    with pytest.raises(ValueError):
        celex_to_entity_id("not-a-celex")


# ── synthetic fragment -> bundle shape ───────────────────────────────────────


def test_parse_synthetic_fragment_recovers_celex_and_body() -> None:
    doc = parse_eu_act_fragment(SYNTHETIC_EU_ACT_XML)
    assert isinstance(doc, EuActDocument)
    assert doc.celex == _SYNTHETIC_CELEX
    # Body text collects BOTH the Formex <P> and the AKN <p> paragraph text.
    assert "synthetic rules for scaffold testing" in doc.body_text
    assert "illustrative only" in doc.body_text


def test_parse_fragment_requires_a_celex() -> None:
    no_celex = b"<ACT><ENACTING.TERMS><P>body</P></ENACTING.TERMS></ACT>"
    with pytest.raises(ValueError):
        parse_eu_act_fragment(no_celex)
    # ...but an explicit celex argument satisfies the identity requirement.
    doc = parse_eu_act_fragment(no_celex, celex=_GDPR_CELEX)
    assert doc.celex == _GDPR_CELEX


def test_decode_eu_body_handles_both_serialisations() -> None:
    text = decode_eu_body_text(SYNTHETIC_EU_ACT_XML)
    # Formex <P> (Article 1 + title) and AKN <p> (Article 2) both land.
    assert "Article 1" in text
    assert "scaffold testing" in text
    assert "illustrative only" in text


def test_decode_eu_body_fail_soft_on_malformed() -> None:
    assert decode_eu_body_text(b"<not well formed") == ""
    assert decode_eu_body_text(b"") == ""


def test_build_bundle_matches_fi_substrate_shape() -> None:
    doc = parse_eu_act_fragment(SYNTHETIC_EU_ACT_XML)
    bundle = build_eu_surface_bundle(doc)

    # Same dataclasses the FI pipeline produces.
    assert isinstance(bundle, SourceSurfaceBundle)
    assert bundle.jurisdiction == "eu"
    assert len(bundle.units) == 1
    unit = bundle.units[0]
    assert isinstance(unit, SourceSurfaceUnit)

    # The unit's work_id is the CELEX canonical id — so a future EU ReferenceLens
    # would mint entity:celex:<CELEX>, landing on the FI frontier node.
    canonical = celex_to_canonical_id(doc.celex)
    assert unit.work_id == canonical
    assert bundle.subject.work_id == canonical

    # raw_text is the coordinate space; the source_ref spans the whole body.
    assert unit.raw_text == doc.body_text
    assert unit.source_ref.char_start == 0
    assert unit.source_ref.char_end == len(unit.raw_text)
    assert unit.source_ref.source_unit_id == unit.source_unit_id

    # Stage-1 bridge: the XML tree travels in metadata (same as FI bundle).
    assert unit.metadata["xml_bytes"] == doc.xml_bytes

    # subject is over the whole work (mirrors FI whole_work scope).
    assert bundle.subject.jurisdiction == "eu"
    assert bundle.subject.scope == {"kind": "whole_work"}
    assert bundle.subject.language == "en"


def test_bundle_work_entity_would_align_with_frontier_node() -> None:
    # The end-to-end join: ingest a synthetic EU act whose CELEX equals the GDPR
    # CELEX the FI registry seeds, and confirm the work-entity id a future EU
    # lens would mint from the bundle's work_id equals the FI frontier node id.
    doc = EuActDocument(celex=_GDPR_CELEX, xml_bytes=b"<ACT/>", body_text="")
    bundle = build_eu_surface_bundle(doc)
    eu_work_entity_id = mint_entity_node_id(bundle.units[0].work_id)
    fi_frontier_node_id = celex_to_entity_id(_GDPR_CELEX)
    assert eu_work_entity_id == fi_frontier_node_id
