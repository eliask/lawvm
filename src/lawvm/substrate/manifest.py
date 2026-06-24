"""``lawvm.pack_manifest.v1`` — the self-describing pack root (§4.1; design §13, §21.3).

A :class:`PackManifest` is a frozen dataclass carrying:

* identity: ``pack_id`` (computed), ``pack_kind``, ``work_ids``,
  ``corpus_version``;
* the canonicalization/selection ``profiles`` and the identity/storage codec;
* a list of per-layer :class:`PackLayer` descriptors (kind, path, row schema,
  codec, dict_id, the uncompressed + storage sha256s, the layer ``root``, and
  ``row_count``);
* the ``roots`` map (``materialization_root`` flat + ``selection_index_root``
  additive split, §2.4, plus the cert spine roots);
* the browse/audit/optional layer-requirement lists;
* a ``provenance`` block (git commit, engine_version, created_at, dirty_tree,
  source-policy ids) that is a **universally hash-excluded member** (§2.2).

``pack_id`` is the root-of-roots: ``LeafHash("pack_manifest",
manifest_without_provenance_and_pack_id)`` (§4.1). ``provenance`` and
``pack_id`` never enter that hash, so a metadata-only keeper republish changes
provenance, **not** the legal-state/selection roots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from lawvm.substrate.canonical_json import JsonValue
from lawvm.substrate.roots import leaf_hash

_PACK_MANIFEST_DOMAIN = "pack_manifest"


@dataclass(frozen=True, slots=True)
class PackLayer:
    """One independently-fetchable pack layer (§4.1 ``layers`` + §5 layout).

    ``root_fn`` declares which named root produced ``root`` (``SeqRoot`` for the
    trace layer, ``SetRoot`` elsewhere) so a checker never relies on JSONL row
    order for set/map layers (§2.3). ``uncompressed_sha256`` is the storage hash
    of the raw ``.jsonl`` bytes; ``storage_sha256`` is the hash of the
    transported (possibly zstd) blob — equal under the v0 ``identity`` codec.
    """

    kind: str
    path: str
    row_schema: str
    codec: str
    dict_id: str
    uncompressed_sha256: str
    storage_sha256: str
    root: str
    root_fn: str
    row_count: int

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        """Plain JSON members in deterministic (sort_keys-stable) form."""
        return {
            "kind": self.kind,
            "path": self.path,
            "row_schema": self.row_schema,
            "codec": self.codec,
            "dict_id": self.dict_id,
            "uncompressed_sha256": self.uncompressed_sha256,
            "storage_sha256": self.storage_sha256,
            "root": self.root,
            "root_fn": self.root_fn,
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class PackProvenance:
    """Run-provenance block — a universally hash-excluded member (§2.2, §4.1).

    Never enters any semantic hash: a metadata-only republish (new commit, new
    ``created_at``) must not perturb ``pack_id`` or any legal-state root.
    """

    lawvm_git_commit: str
    engine_version: str
    source_policy_id: str
    checkable_source_bundle_policy: str
    created_at: str
    dirty_tree: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "lawvm_git_commit": self.lawvm_git_commit,
            "engine_version": self.engine_version,
            "source_policy_id": self.source_policy_id,
            "checkable_source_bundle_policy": self.checkable_source_bundle_policy,
            "created_at": self.created_at,
            "dirty_tree": self.dirty_tree,
        }


@dataclass(frozen=True, slots=True)
class PackManifest:
    """``lawvm.pack_manifest.v1`` — the self-describing pack root (§4.1)."""

    pack_kind: str
    work_ids: tuple[str, ...]
    corpus_version: str
    identity_encoding: str
    storage_codec: str
    dict_id: str
    profiles: tuple[str, ...]
    selection_profiles: tuple[str, ...]
    schemas: Mapping[str, str]
    layers: tuple[PackLayer, ...]
    roots: Mapping[str, str]
    required_layers_for_browse: tuple[str, ...]
    required_layers_for_audit: tuple[str, ...]
    optional_layers: tuple[str, ...]
    provenance: PackProvenance
    supersedes_pack_id: str | None = None
    schema: str = field(default="lawvm.pack_manifest.v1")
    # --- v0 forward-compat reservations (design §24.1) ------------------------ #
    # Two SEPARATE axes reserved as OMIT-WHEN-ABSENT optional members so the
    # structure exists without a schema bump and EVERY existing v0 ``pack_id``
    # stays byte-identical when they are unset (they must NOT appear in
    # ``_hashed_dict()`` / ``to_canonical_dict()`` when ``None`` / empty):
    #   * ``corpus_totality_root`` — root of a ``lawvm.corpus_totality.v0`` object
    #     committing to the corpus-level work-universe + the relativity claim
    #     (``closed_world_claim``). Within-work totality is the checker lens; this
    #     reserves the corpus root a future pack-corpus emits.
    #   * ``signatures`` — a list of ``lawvm.signature_attestation.v1`` subjects
    #     (sign roots, not rows). PKI is deferred (design §24 "do NOT require
    #     signatures in v0"); this reserves the detached ``signatures/`` layer so
    #     a later seal lands WITHOUT a breaking redesign. ``signature_attestation_root``
    #     is the SetRoot over those attestations when present.
    # A genuine break (a member that MUST be present) bumps schema → ``.v2``.
    corpus_totality_root: str | None = None
    signature_attestation_root: str | None = None
    signatures: tuple[str, ...] = ()

    def _hashed_dict(self) -> dict[str, JsonValue]:
        """The manifest body that ``pack_id`` hashes — provenance + pack_id excluded.

        §4.1 / §2.2: ``provenance`` and ``pack_id`` are not members of the
        identity hash. Everything else (schemas, profiles, layers, roots,
        layer-requirement lists, ``supersedes_pack_id``) is.

        The v0 reservations (``corpus_totality_root`` /
        ``signature_attestation_root`` / ``signatures``) are **omit-when-absent**
        (design §24.1): they enter the hashed body ONLY when set, so a v0 pack
        that leaves them unset hashes to the byte-identical ``pack_id`` it had
        before the fields existed (the pack_id-stability invariant).
        """
        body: dict[str, JsonValue] = {
            "schema": self.schema,
            "pack_kind": self.pack_kind,
            "work_ids": list(self.work_ids),
            "corpus_version": self.corpus_version,
            "identity_encoding": self.identity_encoding,
            "storage_codec": self.storage_codec,
            "dict_id": self.dict_id,
            "canonicalization_profiles": list(self.profiles),
            "selection_profiles": list(self.selection_profiles),
            "schemas": dict(self.schemas),
            "layers": [layer.to_canonical_dict() for layer in self.layers],
            "roots": dict(self.roots),
            "required_layers_for_browse": list(self.required_layers_for_browse),
            "required_layers_for_audit": list(self.required_layers_for_audit),
            "optional_layers": list(self.optional_layers),
            "supersedes_pack_id": self.supersedes_pack_id,
        }
        if self.corpus_totality_root is not None:
            body["corpus_totality_root"] = self.corpus_totality_root
        if self.signature_attestation_root is not None:
            body["signature_attestation_root"] = self.signature_attestation_root
        if self.signatures:
            body["signatures"] = list(self.signatures)
        return body

    @property
    def pack_id(self) -> str:
        """Root-of-roots: ``LeafHash("pack_manifest", body_without_provenance)`` (§4.1)."""
        return leaf_hash(_PACK_MANIFEST_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        """The full emitted manifest row body — hashed members + provenance + pack_id.

        ``provenance`` stays **visible** in the row (§2.2) even though it is
        dropped from the identity hash; ``pack_id`` is included as the computed
        self-reference for transport (it is never re-hashed into itself).
        """
        body = self._hashed_dict()
        body["pack_id"] = self.pack_id
        body["provenance"] = self.provenance.to_dict()
        return body
