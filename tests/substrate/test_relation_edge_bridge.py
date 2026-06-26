"""Pins for the FI ``ReferenceSet`` → ``lawvm.legal_relation_edge.v0`` bridge.

The bridge (``lawvm.substrate.relation_edge_bridge``) is the §25.4 integration
that turns the folded FI body cross-reference graph into REAL proof-graded
relation edges. These tests prove:

1. The bridge produces MATRIX-LEGAL edges for resolved / range / ambiguous /
   open resolutions (surface plane + the right evidence class, never legal_state).
2. A RANGE resolution folds to ONE edge carrying ALL_VALID + multiple targets
   — NOT N single-target edges (the §14 set semantics survive).
3. The bridged edges round-trip through the checker (L0.8) as VALID.
4. A deliberately mis-built edge (authority_plane=legal_state) is REJECTED by
   the checker fire-drill — the firewall reaches a real consumer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.reference_sets import fold_reference_set
from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.checker import (
    CheckMode,
    IntegrityVerdict,
    Pack,
    PackLayerData,
    ViolationCode,
    check_pack,
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
from lawvm.substrate.relation_edge_bridge import reference_set_to_relation_edge

CV = "fi:corpus:sha256:testcorpus"


def _mention(
    target: Optional[ProvisionRef],
    *,
    confidence: CiteConfidence,
    surface: str,
    span: Optional[SourceSpan] = None,
) -> ReferenceMention:
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="5"),
        target_provision_ref=target,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=confidence,
        phrase_lemma="test_pattern",
        source_span=span,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


def _bridge(mentions: list[ReferenceMention]) -> dict[str, JsonValue]:
    folded = fold_reference_set(mentions, corpus_version=CV, branch="actual")
    return reference_set_to_relation_edge(
        expression=folded.expression,
        resolution=folded.resolution,
        corpus_version=CV,
    )


# --------------------------------------------------------------------------- #
# 1. Matrix-legal mapping per resolution shape                                 #
# --------------------------------------------------------------------------- #


def test_resolved_single_maps_to_registry_resolved_surface() -> None:
    edge = _bridge(
        [
            _mention(
                ProvisionRef(statute_id="711/2022", section_label="7"),
                confidence=CiteConfidence.EXACT,
                surface="7 §:ssä",
            )
        ]
    )
    assert edge["schema"] == SCHEMA_RELATION_EDGE
    assert edge["relation_kind"] == "citation"
    assert edge["target_set_semantics"] == TargetSetSemantics.SINGLE.value
    assert edge["edge_status"] == EdgeStatus.RESOLVED.value
    assert edge["authority_plane"] == AuthorityPlane.SURFACE.value
    assert edge["verification_level"] == VerificationLevel.REGISTRY_RESOLVED.value
    assert edge["replay_authorized"] is False
    # Matrix-legal by construction.
    assert edge_authority_violation(edge) is None


def test_ambiguous_maps_to_source_asserted_surface() -> None:
    edge = _bridge(
        [
            _mention(
                ProvisionRef(statute_id="711/2022", section_label="7"),
                confidence=CiteConfidence.AMBIGUOUS,
                surface="7 §:ssä",
            )
        ]
    )
    assert edge["target_set_semantics"] == TargetSetSemantics.CANDIDATE_AMBIGUITY.value
    assert edge["edge_status"] == EdgeStatus.AMBIGUOUS.value
    assert edge["authority_plane"] == AuthorityPlane.SURFACE.value
    assert edge["verification_level"] == VerificationLevel.SOURCE_ASSERTED.value
    assert edge["replay_authorized"] is False
    assert edge_authority_violation(edge) is None


def test_open_maps_to_open_source_asserted_surface() -> None:
    edge = _bridge(
        [
            _mention(
                None,
                confidence=CiteConfidence.OPEN,
                surface="muussa laissa",
            )
        ]
    )
    assert edge["target_set_semantics"] == TargetSetSemantics.OPEN.value
    assert edge["edge_status"] == EdgeStatus.OPEN.value
    assert edge["verification_level"] == VerificationLevel.SOURCE_ASSERTED.value
    assert edge["target_set"] == []  # open: referent named, not enumerated
    assert edge_authority_violation(edge) is None


def test_unresolved_no_extension_maps_to_unsupported() -> None:
    edge = _bridge(
        [
            _mention(
                None,
                confidence=CiteConfidence.UNRESOLVED,
                surface="(garbled)",
            )
        ]
    )
    assert edge["target_set_semantics"] == TargetSetSemantics.NO_ENUMERABLE_EXTENSION.value
    assert edge["edge_status"] == EdgeStatus.UNSUPPORTED.value
    assert edge["verification_level"] == VerificationLevel.SOURCE_ASSERTED.value
    assert edge_authority_violation(edge) is None


# --------------------------------------------------------------------------- #
# 2. A RANGE folds to ONE all_valid edge with multiple targets (NOT N edges)   #
# --------------------------------------------------------------------------- #


def test_range_folds_to_one_all_valid_edge_with_multiple_targets() -> None:
    refs = recognize_eu_directive_refs("teollisuuspäästödirektiivin 33—35 artiklassa")
    # The flattened projection is still N rows…
    assert len(refs) == 3
    edge = _bridge([r.mention for r in refs])
    # …but the bridge produces ONE edge.
    assert edge["target_set_semantics"] == TargetSetSemantics.ALL_VALID.value
    assert edge["edge_status"] == EdgeStatus.RESOLVED.value
    targets = edge["target_set"]
    assert isinstance(targets, list)
    assert len(targets) == 3
    assert all(t.endswith(("/33", "/34", "/35")) for t in targets)
    assert edge_authority_violation(edge) is None


# --------------------------------------------------------------------------- #
# 3. Bridged edges round-trip through the checker as VALID                     #
# --------------------------------------------------------------------------- #


def _edges_layer(objects: list[Mapping[str, JsonValue]]) -> PackLayerData:
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
        work_ids=("fi:act:711/2022",),
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


def _edges_only_pack(objects: list[Mapping[str, JsonValue]]) -> Pack:
    from lawvm.substrate.exporter import _KNOWN_SCHEMAS

    edges = _edges_layer(objects)
    layers = {"edges": edges}
    return Pack(manifest=_manifest_for(layers), layers=layers, known_schemas=_KNOWN_SCHEMAS)


def test_bridged_edges_round_trip_through_checker_valid() -> None:
    resolved = _bridge(
        [
            _mention(
                ProvisionRef(statute_id="711/2022", section_label="7"),
                confidence=CiteConfidence.EXACT,
                surface="7 §:ssä",
            )
        ]
    )
    range_refs = recognize_eu_directive_refs(
        "teollisuuspäästödirektiivin 33—35 artiklassa"
    )
    range_edge = _bridge([r.mention for r in range_refs])
    open_edge = _bridge(
        [_mention(None, confidence=CiteConfidence.OPEN, surface="muussa laissa")]
    )
    pack = _edges_only_pack([resolved, range_edge, open_edge])
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity == IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert all(
        v.code != ViolationCode.INVALID_EDGE_AUTHORITY for v in verdict.violations
    )


# --------------------------------------------------------------------------- #
# 4. Fire-drill: a mis-built legal_state edge is REJECTED                       #
# --------------------------------------------------------------------------- #


def test_checker_rejects_misbuilt_legal_state_citation() -> None:
    # A citation that LIES about its plane — claims legal_state while carrying a
    # surface evidence class — must be caught by L0.8 on a real loaded pack.
    bad = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="surface:sha256:deadbeef",
        target_set=("711/2022/7",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,  # the lie
        verification_level=VerificationLevel.SOURCE_ASSERTED,
        replay_authorized=False,
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


def test_bridge_never_emits_legal_state() -> None:
    # The bridge guard means NO input shape can produce a legal_state edge.
    for conf, surface, tgt in [
        (CiteConfidence.EXACT, "7 §", ProvisionRef(statute_id="711/2022", section_label="7")),
        (CiteConfidence.AMBIGUOUS, "7 §", ProvisionRef(statute_id="711/2022", section_label="7")),
        (CiteConfidence.OPEN, "muussa laissa", None),
        (CiteConfidence.UNRESOLVED, "(x)", None),
    ]:
        edge = _bridge([_mention(tgt, confidence=conf, surface=surface)])
        assert edge["authority_plane"] == AuthorityPlane.SURFACE.value
