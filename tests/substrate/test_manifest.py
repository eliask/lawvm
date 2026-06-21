"""Pins for ``lawvm.pack_manifest.v1`` (OBJECT_MODEL_AND_PACK_V0.md §4.1).

``pack_id`` = root-of-roots; provenance is hash-excluded (visible-but-not-hashed);
``to_canonical_dict`` is deterministic and round-trips through the wrapper.
"""

from __future__ import annotations

import dataclasses

from lawvm.substrate.canonical_json import semantic_hash, unwrap_and_verify, wrap_row
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.roots import leaf_hash


def _layer() -> PackLayer:
    return PackLayer(
        kind="base",
        path="base/content_leaves.jsonl",
        row_schema="lawvm.content_leaf.v1",
        codec="identity",
        dict_id="",
        uncompressed_sha256="sha256:aa",
        storage_sha256="sha256:aa",
        root="sha256:base_root",
        root_fn="SetRoot",
        row_count=3,
    )


def _provenance(commit: str = "abc123", created: str = "2026-06-22T00:00:00Z") -> PackProvenance:
    return PackProvenance(
        lawvm_git_commit=commit,
        engine_version="lawvm-0.1",
        source_policy_id="keeper_latest_semantic",
        checkable_source_bundle_policy="archival_exact",
        created_at=created,
        dirty_tree=False,
    )


def _manifest(provenance: PackProvenance | None = None) -> PackManifest:
    return PackManifest(
        pack_kind="work_pack",
        work_ids=("fi:act:301/2004",),
        corpus_version="corpus:2026-06-21",
        identity_encoding="lawvm.canonical_json.v1",
        storage_codec="identity",
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={"lawvm.content_leaf.v1": "sha256:schema1"},
        layers=(_layer(),),
        roots={
            "materialization_root": "sha256:mat",
            "selection_index_root": "sha256:sel",
            "certificate_root": "sha256:cert",
            "source_bundle_root": "sha256:src",
        },
        required_layers_for_browse=("base", "state", "cert"),
        required_layers_for_audit=("base", "state", "trace", "proof", "cert"),
        optional_layers=("surface", "edges", "dict"),
        provenance=provenance or _provenance(),
    )


def test_pack_id_is_root_of_roots() -> None:
    m = _manifest()
    expected = leaf_hash("pack_manifest", m._hashed_dict())
    assert m.pack_id == expected
    assert m.pack_id.startswith("sha256:")


def test_pack_id_excludes_provenance() -> None:
    # A metadata-only republish (new commit + timestamp) must not move pack_id.
    base = _manifest(_provenance("commit-A", "2026-06-22T00:00:00Z"))
    republished = _manifest(_provenance("commit-B", "2026-06-23T09:00:00Z"))
    assert base.pack_id == republished.pack_id


def test_pack_id_moves_with_a_root() -> None:
    base = _manifest()
    moved = dataclasses.replace(
        base,
        roots={**dict(base.roots), "selection_index_root": "sha256:CHANGED"},
    )
    assert base.pack_id != moved.pack_id


def test_hashed_dict_omits_provenance_and_pack_id() -> None:
    body = _manifest()._hashed_dict()
    assert "provenance" not in body
    assert "pack_id" not in body


def test_canonical_dict_keeps_provenance_visible() -> None:
    m = _manifest()
    body = m.to_canonical_dict()
    assert body["provenance"] == m.provenance.to_dict()
    assert body["pack_id"] == m.pack_id
    assert body["schema"] == "lawvm.pack_manifest.v1"


def test_canonical_dict_deterministic() -> None:
    m = _manifest()
    assert semantic_hash(m.to_canonical_dict()) == semantic_hash(m.to_canonical_dict())


def test_manifest_row_roundtrips_through_wrapper() -> None:
    m = _manifest()
    row = wrap_row(m.to_canonical_dict())
    assert unwrap_and_verify(row) == m.to_canonical_dict()


def test_layer_descriptor_is_complete() -> None:
    layer = _layer().to_canonical_dict()
    assert set(layer) == {
        "kind",
        "path",
        "row_schema",
        "codec",
        "dict_id",
        "uncompressed_sha256",
        "storage_sha256",
        "root",
        "root_fn",
        "row_count",
    }


def test_manifest_is_frozen() -> None:
    m = _manifest()
    try:
        m.pack_kind = "mutated"  # ty: ignore[invalid-assignment]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("PackManifest must be frozen")
