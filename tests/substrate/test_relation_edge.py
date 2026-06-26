"""Pins for the universal proof-graded relation edge (design §25).

Covers three things:

1. Schema round-trip + content-addressed ``edge_id`` independent recompute
   (§25.1 / §1.3 — the id is the hash of the body without itself).
2. The §25.3 authority×evidence legality matrix as a pure function: it ACCEPTS
   legal combinations and REJECTS illegal ones (a ``legal_state`` edge carrying
   ``induced_similarity``; a ``source_asserted`` edge claiming ``legal_state``).
3. The checker L0.8 fire-drill: an illegal edge row in a pack yields
   ``INVALID_EDGE_AUTHORITY``, driven through the production ``check`` path
   (the firewall's teeth reach a real consumer). Plus: the existing §14
   cross-work resolution profile still checks VALID.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.checker import (
    Checker,
    CheckMode,
    IntegrityVerdict,
    Pack,
    PackLayerData,
    TopLineVerdict,
    ViolationCode,
    check_pack,
)
from lawvm.substrate.corpus import WorkAnchor, build_corpus_pack, make_cross_work_resolution
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
    recompute_edge_id,
)
from lawvm.substrate.roots import leaf_hash, set_root

CV = "fi:corpus:2026-06-22"


# --------------------------------------------------------------------------- #
# 1. Schema round-trip + edge_id independent recompute.                        #
# --------------------------------------------------------------------------- #


def _surface_citation() -> dict[str, JsonValue]:
    """A legal surface citation: registry_resolved + surface plane (ACCEPTED)."""
    return build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="sha256:" + "a" * 64,
        target_set=("sha256:" + "b" * 64,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV, "branch_id": "actual"},
        corpus_version=CV,
        policy_id="lawvm.resolution.body_xref.v0",
    )


def _legal_state_derivation() -> dict[str, JsonValue]:
    """A legal-state textual derivation: replay_verified + replay authorized."""
    return build_relation_edge(
        relation_kind=RelationKind.VERIFIED_TEXTUAL_DERIVATION,
        source_ref="sha256:" + "c" * 64,
        target_set=("sha256:" + "d" * 64,),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.REPLAY_VERIFIED,
        replay_authorized=True,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV, "branch_id": "actual"},
        corpus_version=CV,
    )


def test_edge_schema_round_trip_and_edge_id_independent_recompute() -> None:
    edge = _surface_citation()
    assert edge["schema"] == SCHEMA_RELATION_EDGE
    declared = edge["edge_id"]
    assert isinstance(declared, str) and declared.startswith("sha256:")

    # INDEPENDENT recompute: hash the body without its edge_id with the same
    # domain + canonical encoding (not the production helper) and match.
    without_id = {k: v for k, v in edge.items() if k != "edge_id"}
    independent = leaf_hash("legal_relation_edge", without_id)
    assert independent == declared
    # The production helper agrees with the independent recompute.
    assert recompute_edge_id(edge) == declared

    # edge_id is a function of CONTENT — changing a field changes the id.
    other = dict(edge)
    other.pop("edge_id")
    other["edge_status"] = EdgeStatus.QUALIFIED.value
    assert leaf_hash("legal_relation_edge", other) != declared


def test_edge_id_is_byte_stable_under_ref_order() -> None:
    """target_set / *_refs are sorted by the builder → order-independent id."""
    a = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="sha256:src",
        target_set=("sha256:y", "sha256:x"),
        target_set_semantics=TargetSetSemantics.ALL_VALID,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
        evidence_refs=("sha256:e2", "sha256:e1"),
    )
    b = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="sha256:src",
        target_set=("sha256:x", "sha256:y"),
        target_set_semantics=TargetSetSemantics.ALL_VALID,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.REGISTRY_RESOLVED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
        evidence_refs=("sha256:e1", "sha256:e2"),
    )
    assert a["edge_id"] == b["edge_id"]


# --------------------------------------------------------------------------- #
# 2. The legality matrix (§25.3) — pure-function accept / reject.              #
# --------------------------------------------------------------------------- #


def test_matrix_accepts_legal_surface_citation() -> None:
    assert edge_authority_violation(_surface_citation()) is None


def test_matrix_accepts_legal_state_replay_verified_derivation() -> None:
    assert edge_authority_violation(_legal_state_derivation()) is None


def test_matrix_rejects_legal_state_with_induced_similarity() -> None:
    """A kinship/induced edge masquerading as legal_state must FAIL (§25.3)."""
    bad = build_relation_edge(
        relation_kind=RelationKind.KINSHIP,
        source_ref="sha256:src",
        target_set=("sha256:tgt",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.INDUCED_SIMILARITY,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
    )
    reason = edge_authority_violation(bad)
    assert reason is not None
    assert "legal_state" in reason and "induced_similarity" in reason


def test_matrix_rejects_source_asserted_on_legal_state() -> None:
    """source_asserted is a weak class → barred from the legal_state plane."""
    bad = build_relation_edge(
        relation_kind=RelationKind.SOURCE_CLAIMED_TRANSPOSITION,
        source_ref="sha256:src",
        target_set=("sha256:tgt",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.SOURCE_ASSERTED,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
    )
    reason = edge_authority_violation(bad)
    assert reason is not None
    assert "source_asserted" in reason


def test_matrix_rejects_legal_state_without_replay_authority() -> None:
    """A strong evidence class on legal_state still needs replay_authorized."""
    bad = dict(_legal_state_derivation())
    bad.pop("edge_id")
    bad["replay_authorized"] = False
    bad["edge_id"] = leaf_hash("legal_relation_edge", bad)
    reason = edge_authority_violation(bad)
    assert reason is not None
    assert "replay_authorized" in reason


def test_matrix_rejects_weak_class_claiming_replay() -> None:
    """A weak evidence class may not claim replay authority even on a weak plane."""
    bad = build_relation_edge(
        relation_kind=RelationKind.CITATION,
        source_ref="sha256:src",
        target_set=("sha256:tgt",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.SURFACE,
        verification_level=VerificationLevel.UNVERIFIED,
        replay_authorized=True,
        edge_status=EdgeStatus.OPEN,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
    )
    reason = edge_authority_violation(bad)
    assert reason is not None
    assert "replay_authorized" in reason


# --------------------------------------------------------------------------- #
# 3. Checker L0.8 fire-drill + §14 profile still VALID.                        #
# --------------------------------------------------------------------------- #

_KNOWN_SCHEMAS = frozenset(
    {SCHEMA_RELATION_EDGE, "lawvm.content_leaf.v1", "lawvm.work.v1"}
)


def _edges_layer(objects: list[Mapping[str, JsonValue]]) -> PackLayerData:
    rows = tuple(wrap_row(obj) for obj in objects)
    hashes = [str(row["object_hash"]) for row in rows]
    root = set_root("edges", hashes)
    return PackLayerData(kind="edges", domain="edges", root_fn="SetRoot", root=root, rows=rows)


def _manifest_for(layers: Mapping[str, PackLayerData]) -> PackManifest:
    descriptors = tuple(
        PackLayer(
            kind=kind,
            path=f"{kind}/{kind}.jsonl",
            row_schema="lawvm.mixed",
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
        work_ids=("fi:act:1/2000",),
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
    edges = _edges_layer(objects)
    layers = {"edges": edges}
    return Pack(manifest=_manifest_for(layers), layers=layers, known_schemas=_KNOWN_SCHEMAS)


def test_checker_accepts_legal_relation_edge_rows() -> None:
    pack = _edges_only_pack([_surface_citation(), _legal_state_derivation()])
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.integrity is IntegrityVerdict.VALID, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert not verdict.has_code(ViolationCode.INVALID_EDGE_AUTHORITY)


def test_checker_fires_on_illegal_edge_authority() -> None:
    """An illegal legal_state+induced_similarity edge → INVALID_EDGE_AUTHORITY."""
    bad = build_relation_edge(
        relation_kind=RelationKind.KINSHIP,
        source_ref="sha256:src",
        target_set=("sha256:tgt",),
        target_set_semantics=TargetSetSemantics.SINGLE,
        authority_plane=AuthorityPlane.LEGAL_STATE,
        verification_level=VerificationLevel.INDUCED_SIMILARITY,
        replay_authorized=False,
        edge_status=EdgeStatus.RESOLVED,
        effective_scope={"corpus_version": CV},
        corpus_version=CV,
    )
    pack = _edges_only_pack([_surface_citation(), bad])
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.top_line_verdict is TopLineVerdict.INVALID_EDGE_AUTHORITY
    assert verdict.has_code(ViolationCode.INVALID_EDGE_AUTHORITY)
    detail = next(
        v.detail for v in verdict.violations if v.code is ViolationCode.INVALID_EDGE_AUTHORITY
    )
    # Self-evidencing: the detail names the offending plane×level pair.
    assert "legal_state" in detail and "induced_similarity" in detail


def test_checker_fires_on_tampered_edge_id() -> None:
    """A stale edge_id (body mutated after id computed) → INVALID_EDGE_AUTHORITY."""
    edge = _surface_citation()
    tampered = dict(edge)
    tampered["edge_status"] = EdgeStatus.BLOCKED.value  # mutate body, keep stale edge_id
    pack = _edges_only_pack([tampered])
    verdict = check_pack(pack, mode=CheckMode.BROWSE)
    assert verdict.has_code(ViolationCode.INVALID_EDGE_AUTHORITY)


def test_existing_cross_work_resolution_carries_typed_edge_fields() -> None:
    """§25.4 — the §14 resolution is the first edge profile and is matrix-legal."""
    source = WorkAnchor("fi:act:1/2000", "sha256:" + "a" * 64, "chapter:1/section:1")
    target = WorkAnchor("fi:act:2/2000", "sha256:" + "b" * 64, "chapter:1/section:7")
    res = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    # Backward-compatible: still a lawvm.overlay.v1 with its resolution_id/status.
    assert res["schema"] == "lawvm.overlay.v1"
    assert res["resolution_status"] == "resolved"
    assert isinstance(res["resolution_id"], str)
    # Now also carries the typed relation-edge mirror fields, matrix-legal.
    assert res["authority_plane"] == AuthorityPlane.SURFACE.value
    assert res["verification_level"] == VerificationLevel.REGISTRY_RESOLVED.value
    assert res["replay_authorized"] is False
    assert edge_authority_violation(res) is None  # type: ignore[arg-type]


def test_corpus_edges_pack_still_checks_valid(tmp_path: Path) -> None:
    """The corpus edges/ output (the §14 profile) still checks VALID-clean."""
    a = _write_work_pack(tmp_path / "A", "fi:act:1/2000", "shared text")
    b = _write_work_pack(tmp_path / "B", "fi:act:2/2000", "shared text")
    source = WorkAnchor("fi:act:1/2000", "sha256:" + "a" * 64, "chapter:1/section:1")
    target = WorkAnchor("fi:act:2/2000", "sha256:" + "b" * 64, "chapter:1/section:7")
    res = make_cross_work_resolution(
        source=source, target=target, surface_expr_text="2/2000 7 §:ssä", corpus_version=CV
    )
    out = tmp_path / "corpus"
    result = build_corpus_pack(
        member_pack_dirs={"A": a, "B": b}, out_dir=out, resolutions=[res], corpus_version=CV
    )
    assert result.n_edges == 1
    # The resolution still uses lawvm.overlay.v1, so the edges layer is tagged
    # unsupported (NOT a relation-edge schema) — pack stays valid, no edge-auth
    # violation fires on the overlay profile.
    pack = _load_pack_for_check(out)
    verdict = Checker(mode=CheckMode.BROWSE).check(pack)
    assert verdict.integrity is IntegrityVerdict.VALID_WITH_UNSUPPORTED_LAYERS, [
        v.to_canonical_dict() for v in verdict.violations
    ]
    assert not verdict.has_code(ViolationCode.INVALID_EDGE_AUTHORITY)


# --------------------------------------------------------------------------- #
# Minimal disk-pack helpers (mirror test_synergy's exporter shape).            #
# --------------------------------------------------------------------------- #

import json  # noqa: E402


def _content_leaf(text: str) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {"schema": "lawvm.content_leaf.v1", "text": text}
    body["content_leaf_hash"] = leaf_hash("content_leaf", dict(body))
    return body


def _write_work_pack(out: Path, work_id: str, text: str) -> Path:
    """Write a minimal single-work pack with a base/ content-leaf store."""
    out.mkdir(parents=True, exist_ok=True)
    base_dir = out / "base"
    base_dir.mkdir(parents=True, exist_ok=True)
    work_body: dict[str, JsonValue] = {"schema": "lawvm.work.v1", "work_id": work_id}
    leaf_body = _content_leaf(text)
    rows = [wrap_row(work_body), wrap_row(leaf_body)]
    with (base_dir / "base.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            fh.write("\n")
    manifest = {
        "object": {
            "schema": "lawvm.pack.work.v0",
            "work_ids": [work_id],
            "pack_id": "sha256:" + "0" * 64,
        }
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return out


def _load_pack_for_check(pack_dir: Path) -> Pack:
    """Load a built corpus pack's base + edges layers into a checker Pack."""
    base_rows = tuple(
        json.loads(line)
        for line in (pack_dir / "base" / "base.jsonl").read_text().splitlines()
        if line.strip()
    )
    base_hashes = [str(r["object_hash"]) for r in base_rows]
    base = PackLayerData(
        kind="base",
        domain="base",
        root_fn="SetRoot",
        root=set_root("base", base_hashes),
        rows=base_rows,
    )
    layers: dict[str, PackLayerData] = {"base": base}
    edges_files = list((pack_dir / "edges").rglob("edges.jsonl"))
    if edges_files:
        edge_rows = tuple(
            json.loads(line)
            for line in edges_files[0].read_text().splitlines()
            if line.strip()
        )
        edge_hashes = [str(r["object_hash"]) for r in edge_rows]
        layers["edges"] = PackLayerData(
            kind="edges",
            domain="edges",
            root_fn="SetRoot",
            root=set_root("edges", edge_hashes),
            rows=edge_rows,
        )
    manifest = _manifest_for(layers)
    return Pack(manifest=manifest, layers=layers, known_schemas=_KNOWN_SCHEMAS)
