"""Multi-statute synergy: cross-work dedup measurement + a shared-store corpus pack.

This module is the ``PROTOTYPE_PLAN_V0.md §16`` acceptance gate — the smallest
test that a per-work pack is a graph **node**, not an island. It proves the four
synergy properties with real artifacts (not prose):

(a) **Content-leaf dedup across works.** :func:`measure_leaf_dedup` intersects
    the ``content_leaf_hash`` / ``object_hash`` sets of N independently-exported
    packs and reports shared-leaf count + the bytes a single shared ``base/``
    leaf store saves vs N independent packs. Because a ``lawvm.content_leaf.v1``
    object carries **no** ``work_id`` (OBJECT_MODEL §4.4 — pure text identity),
    identical text in two works yields a byte-identical ``object_hash`` and
    deduplicates automatically; :func:`build_corpus_pack` realises that store.

(b) **Shared zstd frame / dictionary seam.** The manifest's ``storage_codec`` /
    ``dict_id`` (+ per-layer ``codec`` / ``dict_id`` + the reserved ``dict/``
    layer) are the seam a shared dictionary plugs into later WITHOUT a schema
    change. :func:`shared_dict_seam` documents the exact fields. (zstd itself is
    deferred — PROTOTYPE_PLAN §2; the three-hash split, OBJECT_MODEL §3, keeps
    ``semantic_hash`` invariant under any codec/dict choice.)

(c) **Cross-work resolution in ``edges/``.** :func:`make_cross_work_resolution`
    builds ONE real ``lawvm.overlay.v1`` resolution (design §22.2): a reference
    expression in work A resolving to an address/node in work B, keyed by a
    content-addressed ``resolution_id`` (design §22.1 — never positional).
    :func:`build_corpus_pack` writes it to ``edges/<corpus_version>/edges.jsonl``
    and the offline checker accepts it (the ``edges`` layer is optional, so an
    unknown overlay schema yields ``VALID_WITH_UNSUPPORTED_LAYERS`` — the seam is
    open without weakening any existing L0/L1 check).

(d) **selection_universe_root stability.** :func:`universe_root_of` reads a
    work's ``selection_universe`` object hash + ``selection_key_root`` from disk;
    a corpus that bundles work B does NOT touch work A's bytes, so A's universe
    root is byte-identical with or without B (the per-work roots compose without
    cross-contamination). The exporter's determinism (a re-export of A yields the
    identical universe ``object_hash``) is the underlying guarantee.

The whole module is **additive** — it reads packs the single-work exporter
already produces and emits a NEW corpus pack kind; it does not modify the
exporter, checker, or manifest schemas.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.substrate.canonical_json import JsonValue, wrap_row
from lawvm.substrate.corpus_totality import IncludedMember, build_corpus_totality
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.relation_edge import (
    AuthorityPlane,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    edge_authority_violation,
)
from lawvm.substrate.roots import leaf_hash, set_root

# --------------------------------------------------------------------------- #
# Schema + constants                                                          #
# --------------------------------------------------------------------------- #

SCHEMA_OVERLAY = "lawvm.overlay.v1"  # design §22.2 — generic overlay wrapper
SCHEMA_CONTENT_LEAF = "lawvm.content_leaf.v1"

CORPUS_PACK_KIND = "lawvm.pack.corpus.v0"
IDENTITY_ENCODING = "lawvm.canonical_json.v1"
STORAGE_CODEC = "identity"

_DOMAIN_BASE = "base"
_DOMAIN_EDGES = "edges"
_DOMAIN_RESOLUTION = "resolution"


# --------------------------------------------------------------------------- #
# (a) cross-work content-leaf dedup measurement                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _LeafRow:
    object_hash: str
    content_leaf_hash: str
    row_bytes: int  # serialized JSONL line length (incl. trailing "\n")


def _read_content_leaves(base_jsonl: Path) -> dict[str, _LeafRow]:
    """Return ``object_hash -> _LeafRow`` for every content leaf in a base layer.

    Keyed by ``object_hash`` because that is the wire-level dedup key (two
    works with byte-identical leaf text produce the identical wrapped row).
    """
    out: dict[str, _LeafRow] = {}
    with base_jsonl.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            if not stripped.strip():
                continue
            row = json.loads(stripped)
            body = row.get("object")
            if not isinstance(body, dict) or body.get("schema") != SCHEMA_CONTENT_LEAF:
                continue
            oh = str(row["object_hash"])
            out[oh] = _LeafRow(
                object_hash=oh,
                content_leaf_hash=str(body.get("content_leaf_hash", "")),
                row_bytes=len((stripped + "\n").encode("utf-8")),
            )
    return out


@dataclass(frozen=True)
class LeafDedupReport:
    """The (a) measurement: shared leaves + shared-store byte savings."""

    pack_labels: tuple[str, ...]
    per_pack_distinct: dict[str, int]
    shared_object_hashes: tuple[str, ...]
    n_shared: int
    hash_consistency_ok: bool  # every shared object_hash agrees on content_leaf_hash
    independent_total_bytes: int  # sum of every pack's leaf bytes (duplicates counted)
    shared_store_bytes: int  # union of leaf bytes (each distinct leaf stored once)
    saved_bytes: int
    saved_pct: float

    def summary(self) -> str:
        return (
            f"works={list(self.pack_labels)} | "
            f"distinct={self.per_pack_distinct} | "
            f"shared={self.n_shared} (hashes consistent={self.hash_consistency_ok}) | "
            f"independent={self.independent_total_bytes}B "
            f"shared-store={self.shared_store_bytes}B "
            f"saved={self.saved_bytes}B ({self.saved_pct:.2f}%)"
        )


def measure_leaf_dedup(pack_dirs: dict[str, str | Path]) -> LeafDedupReport:
    """Measure cross-pack content-leaf dedup across ``{label: pack_dir}`` (a).

    A shared-store corpus pack stores each *distinct* leaf ``object_hash`` once
    (the union); two independent packs store the per-pack leaves separately (the
    sum). ``saved = independent_total - shared_store``. Shared leaves resolve by
    *identical hash* — verified by asserting every shared ``object_hash`` agrees
    on its ``content_leaf_hash`` across packs.
    """
    leaves: dict[str, dict[str, _LeafRow]] = {
        label: _read_content_leaves(Path(d) / "base" / "base.jsonl")
        for label, d in pack_dirs.items()
    }
    labels = tuple(leaves.keys())
    per_distinct = {label: len(rows) for label, rows in leaves.items()}

    # Multiset count per object_hash → shared = present in >=2 packs.
    appears_in: dict[str, list[str]] = {}
    union_bytes: dict[str, int] = {}
    for label, rows in leaves.items():
        for oh, row in rows.items():
            appears_in.setdefault(oh, []).append(label)
            union_bytes[oh] = row.row_bytes
    shared = tuple(sorted(oh for oh, where in appears_in.items() if len(where) >= 2))

    # Hash consistency: every shared object_hash must agree on content_leaf_hash.
    consistent = True
    for oh in shared:
        clhs = {leaves[label][oh].content_leaf_hash for label in appears_in[oh]}
        if len(clhs) != 1:
            consistent = False
            break

    independent_total = sum(row.row_bytes for rows in leaves.values() for row in rows.values())
    shared_store = sum(union_bytes.values())
    saved = independent_total - shared_store
    saved_pct = (100.0 * saved / independent_total) if independent_total else 0.0
    return LeafDedupReport(
        pack_labels=labels,
        per_pack_distinct=per_distinct,
        shared_object_hashes=shared,
        n_shared=len(shared),
        hash_consistency_ok=consistent,
        independent_total_bytes=independent_total,
        shared_store_bytes=shared_store,
        saved_bytes=saved,
        saved_pct=saved_pct,
    )


# --------------------------------------------------------------------------- #
# (b) shared zstd frame / dictionary seam                                     #
# --------------------------------------------------------------------------- #


def shared_dict_seam(pack_dir: str | Path) -> dict[str, Any]:
    """Document the exact manifest fields giving the shared-dictionary seam (b).

    Returns the manifest-level ``storage_codec`` / ``dict_id``, the per-layer
    ``codec`` / ``dict_id`` (each layer can independently declare a shared dict),
    and whether the reserved ``dict/`` layer is declared optional. No zstd is
    implemented; the point is that the schema already carries the seam, so a
    shared dictionary lands later WITHOUT a manifest schema bump
    (OBJECT_MODEL §3 three-hash split: a dict swap changes ``storage_blob_hash``
    only, never ``semantic_hash``).
    """
    manifest_row = json.loads((Path(pack_dir) / "manifest.json").read_text(encoding="utf-8"))
    body = manifest_row.get("object", manifest_row)
    layers = body.get("layers", [])
    return {
        "manifest.storage_codec": body.get("storage_codec"),
        "manifest.dict_id": body.get("dict_id"),
        "per_layer.codec": {layer["kind"]: layer.get("codec") for layer in layers},
        "per_layer.dict_id": {layer["kind"]: layer.get("dict_id") for layer in layers},
        "per_layer.storage_sha256_vs_uncompressed": {
            layer["kind"]: layer.get("storage_sha256") == layer.get("uncompressed_sha256")
            for layer in layers
        },
        "dict_layer_reserved_optional": "dict" in tuple(body.get("optional_layers", ())),
    }


# --------------------------------------------------------------------------- #
# (c) cross-work resolution object for the edges/ layer                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WorkAnchor:
    """A resolved point in a work: a struct node + its human address + work_id."""

    work_id: str
    struct_node_id: str
    address: str  # the legacy address path string (e.g. "chapter:4/section:47/subsection:1")


def make_cross_work_resolution(
    *,
    source: WorkAnchor,
    target: WorkAnchor,
    surface_expr_text: str,
    corpus_version: str,
    resolution_policy_id: str = "lawvm.resolution.body_xref.v0",
) -> dict[str, JsonValue]:
    """Build ONE ``lawvm.overlay.v1`` cross-work resolution object (c; design §22.2).

    The body has the design §22.2 shape — ``overlay_kind="reference_resolution"``,
    ``anchor`` at the source ``surface_expr`` level, and a ``target_selector``
    carrying a content-addressed ``resolution_id``. The ``resolution_id`` is
    ``leaf_hash("resolution", {source, target, surface_expr, corpus_version,
    policy})`` — fully determined by the resolution's content, NEVER a positional
    ``expr#N`` (PROTOTYPE_PLAN §7; design §22.1 ``resolution_id``). The target is
    a *real address/node* in work B, so the resolution resolves across works.
    """
    # The surface-expression id is itself content-addressed (anchor identity).
    surface_expr_id = leaf_hash(
        "surface_expr",
        {
            "work_id": source.work_id,
            "struct_node_id": source.struct_node_id,
            "expr_text": surface_expr_text,
        },
    )
    target_selector: dict[str, JsonValue] = {
        "target_work_id": target.work_id,
        "target_struct_node_id": target.struct_node_id,
        "target_address": target.address,
        "target_policy": "current",
    }
    # resolution_id = content hash of the (source expr, target, scope) tuple.
    resolution_id = leaf_hash(
        _DOMAIN_RESOLUTION,
        {
            "surface_expr_id": surface_expr_id,
            "target_selector": target_selector,
            "corpus_version": corpus_version,
            "resolution_policy_id": resolution_policy_id,
        },
    )
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_OVERLAY,
        "overlay_kind": "reference_resolution",
        "anchor": {
            "anchor_kind": "surface_expr",
            "anchor_id": surface_expr_id,
            "work_id": source.work_id,
            "struct_node_id": source.struct_node_id,
            "expr_text": surface_expr_text,
        },
        "target_selector": dict(target_selector, resolution_id=resolution_id),
        "resolution_id": resolution_id,
        "producer": {
            "producer_id": "lawvm.body_xref.v0",
            "producer_version": "v0",
            "determinism": "core_deterministic",
        },
        "authority": {
            "surface_only": True,
            "replay_authorized": False,
            "projection_not_source": False,
        },
        "status": "resolved",
        "scope": {
            "corpus_version": corpus_version,
            "branch_id": "actual",
            "resolution_policy_id": resolution_policy_id,
        },
        "depends_on": [source.struct_node_id, target.struct_node_id],
        "source_refs": [],
        # §25.4 — this §14 cross-work reference resolution is the FIRST profile
        # of the universal lawvm.legal_relation_edge.v0. Carry the typed
        # relation-edge fields so the resolution is self-describingly consistent
        # with the §25.3 authority×evidence legality matrix WITHOUT changing the
        # lawvm.overlay.v1 schema (a consumer that reads the overlay keeps
        # working; the relation-graph view reads these typed mirrors). The
        # surface-only / replay-not-authorized posture maps to:
        #   authority_plane=surface · verification_level=registry_resolved ·
        #   replay_authorized=false — a combination the matrix ACCEPTS.
        "relation_kind": RelationKind.CITATION.value,
        "authority_plane": AuthorityPlane.SURFACE.value,
        "verification_level": VerificationLevel.REGISTRY_RESOLVED.value,
        "replay_authorized": False,
        "target_set_semantics": TargetSetSemantics.SINGLE.value,
    }
    # Self-check: the §14 reference profile must itself satisfy the firewall.
    violation = edge_authority_violation(body)
    if violation is not None:  # pragma: no cover — invariant guard
        raise ValueError(
            f"make_cross_work_resolution emits an edge that violates the §25.3 "
            f"authority×evidence legality matrix: {violation}"
        )
    return body


# --------------------------------------------------------------------------- #
# (d) selection_universe_root read-back                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UniverseRoot:
    """Work A's universe identity, read from disk (d)."""

    work_id: str
    universe_object_hash: str
    selection_key_root: str


def universe_root_of(pack_dir: str | Path) -> UniverseRoot:
    """Read a work's ``selection_universe`` object hash + ``selection_key_root`` (d).

    These are derived purely from the work's own selection facts, so they are
    byte-identical regardless of which other works share a corpus (the per-work
    root composes without cross-contamination).
    """
    state_jsonl = Path(pack_dir) / "state" / "state.jsonl"
    with state_jsonl.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip("\n")
            if not stripped.strip():
                continue
            row = json.loads(stripped)
            body = row.get("object")
            if not isinstance(body, dict):
                continue
            if body.get("schema") == "lawvm.selection_universe.v1":
                return UniverseRoot(
                    work_id=str(body.get("work_id", "")),
                    universe_object_hash=str(row["object_hash"]),
                    selection_key_root=str(body.get("selection_key_root", "")),
                )
    raise ValueError(f"no selection_universe object in {state_jsonl}")


# --------------------------------------------------------------------------- #
# The shared-store corpus pack (ties a, c together into a checkable artifact)  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CorpusPackResult:
    out_dir: str
    pack_id: str
    work_ids: tuple[str, ...]
    n_shared_base_leaves: int
    n_edges: int
    base_root: str
    edges_root: str
    corpus_version: str
    corpus_totality_id: str = ""
    work_universe_root: str = ""


def _read_member_identity(pack_dir: str | Path) -> tuple[tuple[str, ...], str]:
    """Read a member pack's ``work_ids`` + ``pack_id`` from its manifest.

    Used to populate the corpus-totality work-inventory (level A): each member
    contributes ``included`` :class:`WorkInventoryRow` rows keyed by its work_id
    and pointing at its member ``pack_id``.
    """
    manifest_row = json.loads((Path(pack_dir) / "manifest.json").read_text(encoding="utf-8"))
    body = manifest_row.get("object", manifest_row)
    work_ids = tuple(str(w) for w in body.get("work_ids", ()))
    pack_id = str(body.get("pack_id", ""))
    return work_ids, pack_id


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _git_commit() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(Path(__file__).resolve().parent),
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_corpus_pack(
    *,
    member_pack_dirs: dict[str, str | Path],
    out_dir: str | Path,
    resolutions: list[dict[str, JsonValue]],
    corpus_version: str | None = None,
) -> CorpusPackResult:
    """Emit a minimal corpus pack: ONE shared ``base/`` content-leaf store + ``edges/``.

    The corpus pack realises (a) — a single deduped content-leaf store for N
    works (each distinct ``object_hash`` written once) — and (c) — the cross-work
    resolutions in ``edges/<corpus_version>/edges.jsonl``. It is a checkable
    artifact: ``base/`` is ``SetRoot``-rooted (the checker recomputes it),
    ``edges/`` is an optional layer (unknown overlay schema → tagged unsupported,
    pack still ``VALID_WITH_UNSUPPORTED_LAYERS``). The exporter/checker are
    untouched; this writes the same ``{object_hash, object}`` row transport.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cv = corpus_version or f"fi:corpus:{_dt.date.today().isoformat()}"

    # -- corpus-totality work-inventory (level A) — read member identities --- #
    # Each member pack contributes its work_ids → its pack_id as an ``included``
    # WorkInventoryRow. The ``work_universe_root`` MapRoot over these makes a
    # missing / surplus work detectable, exactly as a per-work selection_universe
    # makes a missing row detectable (design §23.x level A, §24.1.3).
    included_members: list[IncludedMember] = []
    for d in member_pack_dirs.values():
        m_work_ids, m_pack_id = _read_member_identity(d)
        for wid in m_work_ids:
            included_members.append(IncludedMember(work_id=wid, pack_id=m_pack_id))

    # -- (a) shared base content-leaf store ---------------------------------- #
    union: dict[str, dict[str, JsonValue]] = {}  # object_hash -> wrapped row
    work_ids: list[str] = []
    for label, d in member_pack_dirs.items():
        base = Path(d) / "base" / "base.jsonl"
        with base.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.rstrip("\n")
                if not stripped.strip():
                    continue
                row = json.loads(stripped)
                body = row.get("object")
                if not isinstance(body, dict):
                    continue
                if body.get("schema") == SCHEMA_CONTENT_LEAF:
                    union[str(row["object_hash"])] = row
                elif body.get("schema") == "lawvm.work.v1":
                    wid = body.get("work_id")
                    if isinstance(wid, str) and wid not in work_ids:
                        work_ids.append(wid)

    base_dir = out / "base"
    base_dir.mkdir(parents=True, exist_ok=True)
    base_path = base_dir / "base.jsonl"
    base_hashes: list[str] = []
    with base_path.open("w", encoding="utf-8") as fh:
        for oh in sorted(union):  # deterministic on-disk order
            fh.write(json.dumps(union[oh], ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            fh.write("\n")
            base_hashes.append(oh)
    base_root = set_root(_DOMAIN_BASE, base_hashes)

    # -- (c) edges/<corpus_version> cross-work resolutions ------------------- #
    edges_rel = f"edges/{cv}/edges.jsonl"
    edges_path = out / edges_rel
    edges_path.parent.mkdir(parents=True, exist_ok=True)
    edges_hashes: list[str] = []
    with edges_path.open("w", encoding="utf-8") as fh:
        for body in resolutions:
            row = wrap_row(body)
            fh.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
            fh.write("\n")
            edges_hashes.append(str(row["object_hash"]))
    edges_root = set_root(_DOMAIN_EDGES, edges_hashes)

    # -- corpus-totality object (level A) ------------------------------------ #
    # Honest v0 posture: an ``observed_crawl`` universe with
    # ``closed_world_claim=false`` — we bundle the works we observed, NOT a signed
    # Finlex enumeration of the jurisdiction. The object commits to the
    # work-universe MapRoot; the manifest reserves ``corpus_totality_root``.
    corpus_totality = build_corpus_totality(
        corpus_id=cv,
        jurisdiction=cv.split(":", 1)[0] if ":" in cv else "fi",
        included=included_members,
    )
    ct_rel = "corpus_totality/corpus_totality.jsonl"
    ct_path = out / ct_rel
    ct_path.parent.mkdir(parents=True, exist_ok=True)
    ct_row = wrap_row(corpus_totality.to_canonical_dict())
    ct_path.write_text(
        json.dumps(ct_row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    corpus_totality_root = str(ct_row["object_hash"])

    # -- manifest ------------------------------------------------------------ #
    base_layer = PackLayer(
        kind="base",
        path="base/base.jsonl",
        row_schema="lawvm.layer.base.v0",
        codec=STORAGE_CODEC,
        dict_id="",
        uncompressed_sha256=_sha256_file(base_path),
        storage_sha256=_sha256_file(base_path),
        root=base_root,
        root_fn="SetRoot",
        row_count=len(base_hashes),
    )
    edges_layer = PackLayer(
        kind="edges",
        path=edges_rel,
        row_schema=SCHEMA_OVERLAY,
        codec=STORAGE_CODEC,
        dict_id="",
        uncompressed_sha256=_sha256_file(edges_path),
        storage_sha256=_sha256_file(edges_path),
        root=edges_root,
        root_fn="SetRoot",
        row_count=len(edges_hashes),
    )
    corpus_totality_layer = PackLayer(
        kind="corpus_totality",
        path=ct_rel,
        row_schema="lawvm.corpus_totality.v0",
        codec=STORAGE_CODEC,
        dict_id="",
        uncompressed_sha256=_sha256_file(ct_path),
        storage_sha256=_sha256_file(ct_path),
        root=set_root("corpus_totality", [corpus_totality_root]),
        root_fn="SetRoot",
        row_count=1,
    )
    provenance = PackProvenance(
        lawvm_git_commit=_git_commit(),
        engine_version="lawvm.engine.replay",
        source_policy_id="archival_exact",
        checkable_source_bundle_policy="archival_exact",
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        dirty_tree=False,
    )
    manifest = PackManifest(
        pack_kind=CORPUS_PACK_KIND,
        work_ids=tuple(work_ids),
        corpus_version=cv,
        identity_encoding=IDENTITY_ENCODING,
        storage_codec=STORAGE_CODEC,
        dict_id="",
        profiles=("lawvm.canon.semantic_text.v1",),
        selection_profiles=("lawvm.selection.governing_text.v1",),
        schemas={
            "content_leaf": SCHEMA_CONTENT_LEAF,
            "overlay": SCHEMA_OVERLAY,
            "corpus_totality": "lawvm.corpus_totality.v0",
        },
        layers=(base_layer, edges_layer, corpus_totality_layer),
        roots={"base_root": base_root, "edges_root": edges_root},
        required_layers_for_browse=("base",),
        required_layers_for_audit=("base",),
        optional_layers=(
            "edges",
            "corpus_totality",
            "surface",
            "branch",
            "overlay",
            "projection",
            "dict",
        ),
        provenance=provenance,
        # The corpus-totality root reservation now CARRIES a value (the level-A
        # work-universe object) — the omit-when-absent manifest member is used.
        corpus_totality_root=corpus_totality_root,
    )
    (out / "manifest.json").write_text(
        json.dumps(
            wrap_row(manifest.to_canonical_dict()),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return CorpusPackResult(
        out_dir=str(out),
        pack_id=manifest.pack_id,
        work_ids=tuple(work_ids),
        n_shared_base_leaves=len(base_hashes),
        n_edges=len(edges_hashes),
        base_root=base_root,
        edges_root=edges_root,
        corpus_version=cv,
        corpus_totality_id=corpus_totality.corpus_totality_id,
        work_universe_root=corpus_totality.work_universe_root,
    )
