"""Typed derivation-edge classifier — the four DISTINCT kinds are never conflated.

This is the proof for the FI-layer transclusion / typed-derivation-edge frontier
capability. It demonstrates, on REAL Finnish witnesses, that the four
categorically-different ways one provision relates to another are emitted as
DISTINCT, separately-typed ``lawvm.legal_relation_edge.v0`` edges — textual ≠
citation ≠ conformance ≠ model-code — honouring the Pro relation-edge rule:

    "deduplication is not authority; shared bytes do not prove shared legal
    origin."

Run:
    uv run pytest tests/test_fi_derivation_edges.py -v

Witnesses:
* TEXTUAL — the standard Finnish ``voimaantulo`` (entry-into-force) clause, which
  is reproduced VERBATIM across thousands of FI acts. Shared bytes, byte-replayable
  — but deliberately NOT a lineage claim (it is boilerplate, not modelling). This
  is precisely the case where conflating textual with model-code would be wrong.
* CONFORMANCE — a real FI transposition declaration of the EU industrial-emissions
  directive (CELEX 32010L0075), extracted by the PRODUCTION
  ``recognize_transposition_claims`` extractor.
* CITATION — a real FI cross-reference surface ("siten kuin 5 §:ssä säädetään").
* MODEL_CODE — a near-copy that resembles but does NOT byte-replay → a
  typed-UNKNOWN resemblance + a residual, NEVER a fabricated derivation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from lawvm.finland.references.derivation_edges import (
    DerivationKind,
    FiProvision,
    classify_relationships,
    classify_textual,
)
from lawvm.finland.references.eu_transposition import (
    TranspositionStatus,
    recognize_transposition_claims,
)
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    RelationKind,
    edge_authority_violation,
    recompute_edge_id,
)

CV = "fi-derivation-edges:test"


def _scope(edge: Mapping[str, object]) -> Mapping[str, object]:
    """Narrow an edge's ``effective_scope`` to a mapping so nested keys are typed."""
    scope = edge["effective_scope"]
    assert isinstance(scope, dict)
    return cast("Mapping[str, object]", scope)


def _str_list(value: object) -> list[object]:
    """Narrow a ``does_not_imply`` value to a list for membership checks."""
    assert isinstance(value, list)
    return cast("list[object]", value)

# --- Real FI voimaantulo boilerplate, reproduced verbatim across two acts. ---
# This exact clause shape appears byte-identically in thousands of Finnish acts.
_VOIMAANTULO = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2014."

# Two acts with an identically-TITLED "Soveltamisala" (scope) section whose
# BODIES diverge entirely — a same-titled section is a classic model-code
# resemblance, but the bodies share too few bytes to be a verbatim copy. This is
# precisely the case the honesty boundary protects: a heading resemblance is NOT
# a lineage proof.
_SOVELTAMISALA_HEADER = "1 § Soveltamisala"
_SCOPE_BODY_A = (
    "Tätä lakia sovelletaan ympäristön pilaantumisen vaaraa aiheuttavaan "
    "toimintaan sekä tällaisen toiminnan sijoittumiseen ja valvontaan."
)
_SCOPE_BODY_B = (
    "Tätä lakia sovelletaan rahanpesun ja terrorismin rahoittamisen "
    "estämiseen ilmoitusvelvollisten harjoittamassa liiketoiminnassa."
)

# A real FI transposition declaration of the industrial-emissions directive.
_TRANSPOSITION_PROSE = (
    "Tällä lailla pannaan täytäntöön teollisuuspäästödirektiivin "
    "III luvun säännösten täytäntöönpanemiseksi tarvittavat toimet."
)


def _act_a_voimaantulo() -> FiProvision:
    return FiProvision(work_id="2014/527", address="40 §", text=_VOIMAANTULO)


def _act_b_voimaantulo() -> FiProvision:
    # A DIFFERENT act reproducing the SAME entry-into-force clause verbatim.
    return FiProvision(work_id="2014/903", address="22 §", text=_VOIMAANTULO)


def _act_a_scope() -> FiProvision:
    return FiProvision(
        work_id="2014/527", address="1 §", text=_SCOPE_BODY_A, header=_SOVELTAMISALA_HEADER
    )


def _act_b_scope() -> FiProvision:
    # SAME heading ("Soveltamisala"), DIFFERENT body — a resemblance, not a copy.
    return FiProvision(
        work_id="2017/444", address="1 §", text=_SCOPE_BODY_B, header=_SOVELTAMISALA_HEADER
    )


# --------------------------------------------------------------------------- #
# (1) The four kinds are emitted DISTINCTLY from real witnesses.              #
# --------------------------------------------------------------------------- #


def _conformance_claims_from_real_extractor() -> list[tuple[str, str | None, str, str]]:
    claims = recognize_transposition_claims(
        _TRANSPOSITION_PROSE, citing_engine_id="2014/527"
    )
    # Keep the RESOLVED industrial-emissions claim (the demonstrative witness).
    out: list[tuple[str, str | None, str, str]] = []
    for c in claims:
        if c.status is TranspositionStatus.RESOLVED:
            out.append(
                (c.citing_engine_id, c.directive_celex, c.directive_surface, c.status.value)
            )
    assert out, "real extractor must bind the industrial-emissions directive"
    return out


def test_four_distinct_kinds_from_real_witnesses() -> None:
    citing = _act_a_voimaantulo()
    edges = classify_relationships(
        # Verbatim voimaantulo → textual; same-titled diverging scope → model_code.
        textual_candidates=[
            (_act_a_voimaantulo(), _act_b_voimaantulo()),
            (_act_a_scope(), _act_b_scope()),
        ],
        conformance_claims=_conformance_claims_from_real_extractor(),
        citations=[(citing, "fi-provision:2014/527#5 §", True)],
        corpus_version=CV,
    )

    # Each kind landed in its OWN list — non-conflation by construction.
    assert len(edges.textual) == 1, edges.textual
    assert len(edges.model_code) == 1, edges.model_code
    assert len(edges.citation) == 1, edges.citation
    # Conformance = TWO distinct edges: the act's CLAIM + the absence-of-assessment.
    assert len(edges.conformance) == 2, edges.conformance

    # All FOUR distinct kinds are present, and every emitted edge classifies back
    # to EXACTLY one kind.
    kinds = {edges.kind_of(e) for e in edges.all_edges()}
    assert kinds == {
        DerivationKind.TEXTUAL,
        DerivationKind.MODEL_CODE,
        DerivationKind.CONFORMANCE,
        DerivationKind.CITATION,
    }, kinds


def test_textual_is_legal_state_and_byte_reproducible() -> None:
    edges = classify_relationships(
        textual_candidates=[(_act_a_voimaantulo(), _act_b_voimaantulo())],
        corpus_version=CV,
    )
    (edge,) = edges.textual
    assert edge["relation_kind"] == RelationKind.VERIFIED_TEXTUAL_DERIVATION.value
    assert edge["authority_plane"] == AuthorityPlane.LEGAL_STATE.value
    assert edge["replay_authorized"] is True
    scope = _scope(edge)
    assert scope["derivation_kind"] == DerivationKind.TEXTUAL.value
    assert scope["replay_reproduces_target"] is True
    # THE HONESTY RULE, encoded in the edge: shared wording, NOT lineage etc.
    assert scope["means"] == "shared_wording_byte_reproducible"
    assert scope["does_not_imply"] == [
        "model_code_lineage",
        "eu_conformance",
        "citation",
    ]


def test_citation_is_surface_plane_never_legal_state() -> None:
    citing = _act_a_voimaantulo()
    edges = classify_relationships(
        citations=[(citing, "fi-provision:2014/527#5 §", True)],
        corpus_version=CV,
    )
    (edge,) = edges.citation
    assert edge["relation_kind"] == RelationKind.CITATION.value
    assert edge["authority_plane"] == AuthorityPlane.SURFACE.value
    assert edge["replay_authorized"] is False
    scope = _scope(edge)
    # A pointer says NOTHING about shared wording or lineage.
    dni = _str_list(scope["does_not_imply"])
    assert "shared_wording" in dni
    assert "model_code_lineage" in dni


def test_conformance_pair_records_claim_and_absence_of_assessment() -> None:
    edges = classify_relationships(
        conformance_claims=_conformance_claims_from_real_extractor(),
        corpus_version=CV,
    )
    by_kind = {e["relation_kind"]: e for e in edges.conformance}
    claim = by_kind[RelationKind.SOURCE_CLAIMED_TRANSPOSITION.value]
    absence = by_kind[RelationKind.CONFORMANCE_ASSESSMENT.value]

    # The act's CLAIM is source-asserted evidence — never a verified conformance.
    assert claim["authority_plane"] == AuthorityPlane.EVIDENCE.value
    assert claim["target_set"] == ["eu-directive:32010L0075"]
    assert _scope(claim)["claim"] == "act_declares_transposition"

    # The absence edge records that NO assessment exists — status open.
    assert absence["authority_plane"] == AuthorityPlane.OVERLAY.value
    assert absence["status"] == "open"
    assert _scope(absence)["assessment_present"] is False
    assert _scope(absence)["means"] == "conformance_not_assessed"


# --------------------------------------------------------------------------- #
# (2) The honesty boundary — shared bytes never become a lineage claim.       #
# --------------------------------------------------------------------------- #


def test_shared_bytes_do_not_become_model_code_lineage() -> None:
    """Two acts share the voimaantulo clause verbatim. They get a TEXTUAL edge —
    NOT a model-code lineage edge. Shared bytes ≠ shared legal origin."""
    edges = classify_relationships(
        textual_candidates=[(_act_a_voimaantulo(), _act_b_voimaantulo())],
        corpus_version=CV,
    )
    assert len(edges.textual) == 1
    # NO model-code edge was fabricated from the byte match.
    assert edges.model_code == []


def test_same_titled_diverging_body_is_typed_unknown_resemblance_plus_residual() -> None:
    """Two acts share a section heading ("Soveltamisala") but their bodies diverge
    entirely. A textual-axis test does NOT byte-replay → owned as a model-code
    RESEMBLANCE (lineage typed-UNKNOWN) + a typed residual, never a fabricated
    textual derivation. A heading resemblance is not a lineage proof."""
    result = classify_textual(
        source=_act_a_scope(),
        target=_act_b_scope(),
        corpus_version=CV,
    )
    # No legal-state textual edge — bodies are not a verbatim copy.
    assert result.textual == []
    # Exactly one resemblance edge, typed-UNKNOWN as to lineage.
    (edge,) = result.model_code
    assert edge["relation_kind"] == RelationKind.KINSHIP.value
    assert edge["authority_plane"] == AuthorityPlane.OVERLAY.value
    scope = _scope(edge)
    assert scope["derivation_kind"] == DerivationKind.MODEL_CODE.value
    assert scope["lineage_decided"] is False
    assert scope["lineage_basis"] == "bytes_only_not_lineage"
    # And a self-evidencing residual was recorded — nothing dropped silently.
    assert len(result.residuals) == 1


def test_model_code_resemblance_never_claims_verified_derivation() -> None:
    result = classify_textual(
        source=_act_a_scope(),
        target=_act_b_scope(),
        corpus_version=CV,
    )
    (edge,) = result.model_code
    assert "verified_textual_derivation" in _str_list(_scope(edge)["does_not_imply"])


# --------------------------------------------------------------------------- #
# (3) Every edge is matrix-legal (§25.3) and content-addressed.               #
# --------------------------------------------------------------------------- #


def test_every_emitted_edge_is_matrix_legal() -> None:
    edges = classify_relationships(
        textual_candidates=[(_act_a_voimaantulo(), _act_b_voimaantulo())],
        conformance_claims=_conformance_claims_from_real_extractor(),
        citations=[(_act_a_voimaantulo(), "fi-provision:2014/527#5 §", True)],
        corpus_version=CV,
    )
    for edge in edges.all_edges():
        assert edge_authority_violation(edge) is None, edge


def test_every_edge_id_recomputes() -> None:
    edges = classify_relationships(
        textual_candidates=[(_act_a_voimaantulo(), _act_b_voimaantulo())],
        conformance_claims=_conformance_claims_from_real_extractor(),
        citations=[(_act_a_voimaantulo(), "fi-provision:2014/527#5 §", True)],
        corpus_version=CV,
    )
    for edge in edges.all_edges():
        assert edge["edge_id"] == recompute_edge_id(edge), edge


def test_a_kinship_edge_can_never_pose_as_legal_state() -> None:
    """The matrix guard physically forbids a model-code resemblance from being
    minted on the legal_state plane — the non-conflation has teeth."""
    result = classify_textual(
        source=_act_a_scope(),
        target=_act_b_scope(),
        corpus_version=CV,
    )
    (edge,) = result.model_code
    forged = dict(edge)
    forged["authority_plane"] = AuthorityPlane.LEGAL_STATE.value
    # Forging a resemblance onto the legal_state plane is a matrix violation.
    assert edge_authority_violation(forged) is not None
