"""Pins for the FI EU-directive transposition → relation-edge bridge (§25.8).

The bridge (``lawvm.substrate.eu_transposition_bridge``) maps a
:class:`~lawvm.finland.references.eu_transposition.TranspositionClaim` to the
deterministic, verifiable EU directive relation edges — and ONLY those. These
tests prove:

1. A bound claim → a matrix-legal ``source_claimed_transposition`` edge
   (evidence plane + source_asserted, NEVER legal_state, replay=false).
2. ``timeliness_fact`` computes on_time / late correctly from the deadline seed
   vs commencement, and degrades to an honest ``open`` "deadline_unknown" when
   the directive has no seeded deadline — never a fabricated date.
3. The conformance edge is ALWAYS the "not assessed" residual (open / overlay /
   external_assessment) — NEVER a positive correct/incorrect-transposition
   conclusion, for ANY claim shape.
4. A named-but-unbound (statute_only / ambiguous) claim still produces edges with
   the directive surface preserved and ``celex`` absent — never dropped.
5. The bridged edges round-trip through the checker (L0.8) as VALID, and a
   deliberately mis-built legal_state transposition edge is REJECTED
   (INVALID_EDGE_AUTHORITY) — the firewall reaches a real consumer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from lawvm.finland.references.eu_transposition import (
    TranspositionClaim,
    TranspositionStatus,
)
from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    Pack,
    PackLayerData,
    ViolationCode,
    check_pack,
)
from lawvm.substrate.eu_transposition_bridge import (
    claimed_transposition_edge,
    conformance_not_assessed_edge,
    timeliness_edge,
    transposition_claim_to_edges,
)
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.relation_edge import (
    SCHEMA_RELATION_EDGE,
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
)

CV = "fi:corpus:sha256:testcorpus"


def _effective_scope(edge: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    scope = edge["effective_scope"]
    assert isinstance(scope, dict)
    return cast(Mapping[str, JsonValue], scope)


def _claim(
    *,
    celex: str | None = "32010L0075",
    surface: str = "teollisuuspäästödirektiivin",
    status: TranspositionStatus = TranspositionStatus.RESOLVED,
    engine_id: str = "2014/527",
) -> TranspositionClaim:
    return TranspositionClaim(
        citing_engine_id=engine_id,
        directive_celex=celex,
        directive_surface=surface,
        claim_surface="täytäntöönpanemiseksi",
        char_start=0,
        char_end=10,
        transposition_status=status,
    )


# --------------------------------------------------------------------------- #
# 1. source_claimed_transposition — evidence/source_asserted, never legal_state #
# --------------------------------------------------------------------------- #


def test_claimed_transposition_is_matrix_legal_evidence_edge() -> None:
    edge = claimed_transposition_edge(_claim(), corpus_version=CV)
    assert edge["relation_kind"] == RelationKind.SOURCE_CLAIMED_TRANSPOSITION.value
    assert edge["authority_plane"] == AuthorityPlane.EVIDENCE.value
    assert edge["verification_level"] == VerificationLevel.SOURCE_ASSERTED.value
    assert edge["replay_authorized"] is False
    assert edge["edge_status"] == EdgeStatus.RESOLVED.value
    assert edge["target_set"] == ["celex:32010L0075"]
    # Matrix-legal by construction.
    assert edge_authority_violation(edge) is None


def test_claimed_transposition_never_legal_state_for_any_shape() -> None:
    for status, celex in [
        (TranspositionStatus.RESOLVED, "32010L0075"),
        (TranspositionStatus.AMBIGUOUS, None),
        (TranspositionStatus.STATUTE_ONLY, None),
    ]:
        edge = claimed_transposition_edge(
            _claim(celex=celex, status=status), corpus_version=CV
        )
        assert edge["authority_plane"] == AuthorityPlane.EVIDENCE.value
        assert edge["authority_plane"] != AuthorityPlane.LEGAL_STATE.value
        assert edge_authority_violation(edge) is None


def test_unbound_claim_preserves_surface_and_drops_no_directive() -> None:
    edge = claimed_transposition_edge(
        _claim(celex=None, surface="päästökattodirektiivin", status=TranspositionStatus.STATUTE_ONLY),
        corpus_version=CV,
    )
    # The named-but-unbound directive surface is preserved (tag, don't guess);
    # the target carries the nickname, never a fabricated CELEX.
    assert edge["target_set"] == ["eu-nickname:päästökattodirektiivin"]
    assert edge["edge_status"] == EdgeStatus.QUALIFIED.value
    scope = _effective_scope(edge)
    assert scope["binding_status"] == "statute_only"


# --------------------------------------------------------------------------- #
# 2. timeliness_fact — on_time / late / deadline_unknown                        #
# --------------------------------------------------------------------------- #


def test_timeliness_late_when_commencement_after_deadline() -> None:
    # IED deadline 2013-01-07; this act commences 2014-09-01 → late.
    edge = timeliness_edge(_claim(), commencement_date="2014-09-01", corpus_version=CV)
    assert edge["verification_level"] == VerificationLevel.DATE_COMPUTABLE.value
    assert edge["authority_plane"] == AuthorityPlane.EVIDENCE.value
    assert edge["replay_authorized"] is False
    assert edge["edge_status"] == EdgeStatus.RESOLVED.value
    scope = _effective_scope(edge)
    assert scope["timeliness_verdict"] == "late"
    assert scope["transposition_deadline"] == "2013-01-07"
    assert edge_authority_violation(edge) is None


def test_timeliness_on_time_when_commencement_before_deadline() -> None:
    edge = timeliness_edge(_claim(), commencement_date="2012-06-01", corpus_version=CV)
    scope = _effective_scope(edge)
    assert scope["timeliness_verdict"] == "on_time"
    assert edge["edge_status"] == EdgeStatus.RESOLVED.value


def test_timeliness_open_when_deadline_unknown_no_fabricated_date() -> None:
    # A bound directive with NO seeded deadline → honest open "deadline_unknown".
    edge = timeliness_edge(
        _claim(celex="32099L9999"), commencement_date="2020-01-01", corpus_version=CV
    )
    assert edge["edge_status"] == EdgeStatus.OPEN.value
    scope = _effective_scope(edge)
    assert scope["timeliness_verdict"] == "deadline_unknown"
    assert scope["transposition_deadline"] is None  # never fabricated
    assert edge_authority_violation(edge) is None


def test_timeliness_open_when_directive_unbound() -> None:
    edge = timeliness_edge(
        _claim(celex=None, status=TranspositionStatus.STATUTE_ONLY),
        commencement_date="2020-01-01",
        corpus_version=CV,
    )
    assert edge["edge_status"] == EdgeStatus.OPEN.value
    scope = _effective_scope(edge)
    assert scope["timeliness_verdict"] == "deadline_unknown"


# --------------------------------------------------------------------------- #
# 3. conformance — ALWAYS "not assessed", NEVER a positive conclusion           #
# --------------------------------------------------------------------------- #


def test_conformance_is_always_not_assessed_residual() -> None:
    for status, celex in [
        (TranspositionStatus.RESOLVED, "32010L0075"),
        (TranspositionStatus.AMBIGUOUS, None),
        (TranspositionStatus.STATUTE_ONLY, None),
    ]:
        edge = conformance_not_assessed_edge(
            _claim(celex=celex, status=status), corpus_version=CV
        )
        assert edge["relation_kind"] == RelationKind.CONFORMANCE_ASSESSMENT.value
        # The absence of an assessment — open, overlay, external_assessment.
        assert edge["edge_status"] == EdgeStatus.OPEN.value
        assert edge["authority_plane"] == AuthorityPlane.OVERLAY.value
        assert edge["verification_level"] == VerificationLevel.EXTERNAL_ASSESSMENT.value
        assert edge["replay_authorized"] is False
        scope = _effective_scope(edge)
        assert scope["conformance"] == "not_assessed"
        assert edge_authority_violation(edge) is None


def test_no_positive_conformance_edge_is_ever_produced() -> None:
    # The full edge set for a claim carries exactly ONE conformance edge, and it
    # is the "not assessed" residual — never a "resolved"/"correct"/"breach" one.
    edges = transposition_claim_to_edges(
        _claim(), commencement_date="2014-09-01", corpus_version=CV
    )
    conformance = [
        e for e in edges if e["relation_kind"] == RelationKind.CONFORMANCE_ASSESSMENT.value
    ]
    assert len(conformance) == 1
    assert conformance[0]["edge_status"] == EdgeStatus.OPEN.value
    # No conformance edge is ever RESOLVED (a resolved conformance would be a
    # substantive correct/incorrect-transposition conclusion — forbidden §25.8).
    assert all(c["edge_status"] != EdgeStatus.RESOLVED.value for c in conformance)


def test_claim_yields_exactly_three_edge_kinds() -> None:
    edges = transposition_claim_to_edges(
        _claim(), commencement_date="2014-09-01", corpus_version=CV
    )
    kinds = [e["relation_kind"] for e in edges]
    assert kinds == [
        RelationKind.SOURCE_CLAIMED_TRANSPOSITION.value,
        RelationKind.TIMELINESS_FACT.value,
        RelationKind.CONFORMANCE_ASSESSMENT.value,
    ]


# --------------------------------------------------------------------------- #
# 4. Checker round-trip (L0.8) + fire-drill                                     #
# --------------------------------------------------------------------------- #


def _edges_layer(objects: Sequence[Mapping[str, JsonValue]]) -> PackLayerData:
    from lawvm.substrate.roots import set_root

    rows = tuple(wrap_row(obj) for obj in objects)
    hashes = [str(row["object_hash"]) for row in rows]
    root = set_root("edges", hashes)
    return PackLayerData(kind="edges", domain="edges", root_fn="SetRoot", root=root, rows=rows)


def _manifest_for(layers: Mapping[str, PackLayerData]) -> PackManifest:
    descriptors = tuple(
        PackLayer(
            kind=kind,
            path=f"{kind}/{kind}.jsonl",
            row_schema=SCHEMA_RELATION_EDGE,
            codec="identity",
            dict_id="",
            uncompressed_sha256="sha256:aa",
            storage_sha256="sha256:aa",
            root=data.root,
            root_fn=data.root_fn,
            row_count=len(data.rows),
        )
        for kind, data in layers.items()
    )
    return PackManifest(
        pack_kind="corpus_pack",
        work_ids=("fi:act:527/2014",),
        corpus_version=CV,
        identity_encoding="lawvm.canonical_json.v1",
        storage_codec="identity",
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={SCHEMA_RELATION_EDGE: "sha256:schema_edge"},
        layers=descriptors,
        roots={
            "materialization_root": "sha256:mat",
            "selection_index_root": "sha256:sel",
            "certificate_root": "sha256:cert",
            "source_bundle_root": "sha256:src",
        },
        required_layers_for_browse=("base",),
        required_layers_for_audit=("base",),
        optional_layers=("edges",),
        provenance=PackProvenance(
            lawvm_git_commit="abc123",
            engine_version="lawvm-0.1",
            source_policy_id="keeper_latest_semantic",
            checkable_source_bundle_policy="archival_exact",
            created_at="2026-06-22T00:00:00Z",
            dirty_tree=False,
        ),
    )


def _edges_only_pack(objects: Sequence[Mapping[str, JsonValue]]) -> Pack:
    from lawvm.substrate.exporter import _KNOWN_SCHEMAS

    edges = _edges_layer(objects)
    layers = {"edges": edges}
    return Pack(manifest=_manifest_for(layers), layers=layers, known_schemas=_KNOWN_SCHEMAS)


def test_transposition_edges_round_trip_through_checker_valid() -> None:
    edges = transposition_claim_to_edges(
        _claim(), commencement_date="2014-09-01", corpus_version=CV
    )
    pack = _edges_only_pack(edges)
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity == IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert all(
        v.code != ViolationCode.INVALID_EDGE_AUTHORITY for v in verdict.violations
    )


def test_checker_rejects_misbuilt_legal_state_transposition() -> None:
    # A transposition edge that LIES about its plane (claims legal_state while
    # carrying an evidence/date_computable class) must be caught by L0.8.
    bad = build_relation_edge(
        relation_kind=RelationKind.TIMELINESS_FACT,
        source_ref="fi:act:527/2014",
        target_set=("celex:32010L0075",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,  # the lie
        verification_level=VerificationLevel.DATE_COMPUTABLE,
        replay_authorized=False,  # legal_state requires replay=true → illegal
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"branch_id": "actual"},
        corpus_version=CV,
    )
    pack = _edges_only_pack([bad])
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity == IntegrityVerdict.INVALID_EDGE_AUTHORITY
    assert any(
        v.code == ViolationCode.INVALID_EDGE_AUTHORITY for v in verdict.violations
    )
