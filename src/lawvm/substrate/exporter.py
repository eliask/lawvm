"""The ``pack-work`` exporter — engine replay → sparse certified pack (P3).

This is the integration payoff of the distributable-substrate program: it runs
the LawVM replay engine once for one work (via the jurisdiction adapter, exactly
as ``export_transition_graph`` does), then re-materializes the point-in-time tree
at every change-date and emits a **sparse, content-addressed, certified pack**
that the offline :mod:`lawvm.substrate.checker` validates without ever running
the replay kernel.

Sparse, not dense (``SUBSTRATE_INTEGRATION_MAP.md §8 gotcha 3``): the pack carries
NO ``active_at`` / ``display_nodes`` / SQLite / per-date duplicated text. The
80x win is content-leaf dedup — ~thousands of distinct text subtrees instead of
~hundreds of thousands of active rows. The selection layer carries one MAXIMAL
constant interval per address, never one row per date.

Identity discipline (the map's gotchas):

* every semantic hash flows through the substrate ``canonical_json_bytes``
  (``ensure_ascii=True``) — NEVER the engine's ``_subtree_json``
  (``ensure_ascii=False``) projection path;
* engine hashes are **bare hex**; this module wraps them to ``"sha256:"`` at
  construction and never double-prefixes;
* content-leaf identity is text-only (``irnode_to_text`` → NFC), distinct from
  the structural subtree hash used for transition / checkpoint lineage;
* the build streams JSONL (Rikoslaki is the worst case) — it never holds all
  rendered dates in memory.

The exporter writes one JSONL file per layer kind under ``<out>/`` in the v0
``identity`` codec (raw JSONL, no zstd yet) and a ``cert/certificate.json``
singleton, plus a top-level ``manifest.json``. The overlay-family directories
(``surface/`` ``edges/`` ``branch/`` ``overlay/`` ``projection/`` ``dict/``) are
reserve-created empty so omission of a whole family is itself committed to.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import (
    irnode_to_text,
    structural_subtree_hash,
)
from lawvm.substrate.canonical_json import (
    JsonValue,
    nfc,
    wrap_row,
)
from lawvm.substrate.checker import assemble_manifest_roots
from lawvm.substrate.manifest import (
    PackLayer,
    PackManifest,
    PackProvenance,
)
from lawvm.substrate.relation_edge import SCHEMA_RELATION_EDGE
from lawvm.substrate.roots import (
    leaf_hash,
    map_root,
    seq_root,
    set_root,
)
from lawvm.substrate.selection import (
    ApplicabilityFact,
    DecisionBasis,
    PROFILE_GOVERNING_TEXT,
    ScopePredicate,
    SelectionCandidate,
    SelectionCandidateSet,
    SelectionRow,
    SelectionUniverse,
    TemporalBasis,
    v0_profiles,
)
from lawvm.substrate.source import (
    InitialStateEvent,
)

# --------------------------------------------------------------------------- #
# Constants                                                                    #
# --------------------------------------------------------------------------- #

PACK_KIND = "lawvm.pack.work.v0"
IDENTITY_ENCODING = "lawvm.canonical_json.v1"
STORAGE_CODEC = "identity"
CANON_PROFILE = "lawvm.canon.semantic_text.v1"

# Legacy FI address identity schema (map §8 gotcha 6 — never renamed in place).
ADDRESS_ID_SCHEMA = "lawvm.address_id.fi.legacy_legal_address.v0"
ADDRESS_PROFILE_ID = "lawvm.addrprofile.fi.v0"

# Schemas authored here (no substrate dataclass yet — built inline).
SCHEMA_WORK = "lawvm.work.v1"
SCHEMA_ADDRESS_NODE = "lawvm.address_node.v1"
SCHEMA_CONTENT_LEAF = "lawvm.content_leaf.v1"
SCHEMA_NODE_VERSION = "lawvm.node_version.v1"
SCHEMA_CHECKPOINT = "lawvm.materialization_checkpoint.v1"
SCHEMA_TRANSITION = "lawvm.certified_tree_transition.v1"
SCHEMA_FINDING = "lawvm.finding.v1"
SCHEMA_RESIDUAL = "lawvm.residual.v1"
SCHEMA_COVERAGE = "lawvm.coverage_row.v1"
SCHEMA_CERTIFICATE = "lawvm.certificate.v0"
SCHEMA_SOURCE_ARTIFACT = "lawvm.source_artifact.v1"

# Leaf-hash domains (one per object family; never reused across kinds).
_DOMAIN_WORK = "work"
_DOMAIN_ADDRESS_NODE = "struct_node"
_DOMAIN_CONTENT_LEAF = "content_leaf"
_DOMAIN_NODE_VERSION = "node_version"
_DOMAIN_CHECKPOINT = "state_root"
_DOMAIN_TRANSITION = "certified_tree_transition"
_DOMAIN_FINDING = "finding"
_DOMAIN_RESIDUAL = "residual"
_DOMAIN_COVERAGE = "coverage_row"
_DOMAIN_SOURCE_ARTIFACT = "source_artifact"

# v0 single-branch / single-account / single-profile selection axis.
_BRANCH_ID = "actual"
_PROFILE_ID = PROFILE_GOVERNING_TEXT
_RAIL_PERMANENT = "permanent"
_RAIL_TEMPORARY = "temporary"

# The genesis transition anchor (original_enactment has no snapshot creation id).
_OPEN_END: str | None = None


# The directory layout (map §7). Filled layers carry a JSONL file; reserved
# families are created empty so a whole-family omission is committed to.
_FILLED_LAYERS: tuple[tuple[str, str, str], ...] = (
    # (layer_kind, jsonl_filename, root_fn)
    ("base", "base/base.jsonl", "SetRoot"),
    ("state", "state/state.jsonl", "SetRoot"),
    ("trace", "trace/trace.jsonl", "SeqRoot"),
    ("proof", "proof/proof.jsonl", "SetRoot"),
)
_RESERVED_DIRS: tuple[str, ...] = (
    "surface",
    "edges",
    "branch",
    "overlay",
    "projection",
    "dict",
)


def _wrap_to_prefixed(bare_or_prefixed: str) -> str:
    """Wrap a bare engine hex digest to ``"sha256:"`` without double-prefixing."""
    if bare_or_prefixed.startswith("sha256:"):
        return bare_or_prefixed
    return "sha256:" + bare_or_prefixed


def _interval(start: str, end: str | None) -> tuple[str, str | None]:
    return (start, end)


_DOMAIN_CORPUS_VERSION = "corpus_version"


def _corpus_version(
    *,
    jurisdiction: str,
    work_id: str,
    title: str,
    change_dates: list[str],
) -> str:
    """Deterministic ``corpus_version`` derived from the engine input (Q6 fix).

    Returns ``"{jur}:corpus:sha256:<hex>"`` where the digest pins the work's
    identity + temporal frontier (``work_id``, NFC ``title``, the ordered
    change-date list whose first entry is the commencement). It is a pure
    function of the replay input, so the SAME engine input yields the SAME
    ``corpus_version`` — and therefore the SAME ``pack_id`` — on any day and for
    any third party re-exporting identical input. (The wall-clock republish
    timestamp survives only on the hash-excluded ``provenance.created_at``.)
    """
    digest = leaf_hash(
        _DOMAIN_CORPUS_VERSION,
        {
            "jurisdiction": jurisdiction,
            "work_id": work_id,
            "title": nfc(title),
            "change_dates": list(change_dates),
        },
    )
    return f"{jurisdiction}:corpus:{digest}"


# --------------------------------------------------------------------------- #
# Inline object builders (families with no substrate dataclass)                #
# --------------------------------------------------------------------------- #


def _work_body(work_id: str, title: str, jurisdiction: str, corpus_version: str) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_WORK,
        "work_id": work_id,
        "jurisdiction": jurisdiction,
        "title": nfc(title),
        "corpus_version": corpus_version,
    }
    body["work_object_id"] = leaf_hash(_DOMAIN_WORK, _without(body, "work_object_id"))
    return body


def _address_node_body(
    work_id: str,
    address_path: str,
    structural_kind: str,
) -> dict[str, JsonValue]:
    """``lawvm.address_node.v1`` — the stable structural node (map §2).

    ``struct_node_id`` is the legacy FI address identity (map §8 gotcha 6); the
    address path is carried JSON-safe as the canonical ``str(LegalAddress)``
    rendering (``chapter:2/section:7``).
    """
    identity = {
        "identity_schema": ADDRESS_ID_SCHEMA,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "special": "",
        "creation_event_id": "",
        "local_discriminator": "",
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
    }
    struct_node_id = leaf_hash(_DOMAIN_ADDRESS_NODE, identity)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_ADDRESS_NODE,
        "struct_node_id": struct_node_id,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "identity_schema": ADDRESS_ID_SCHEMA,
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
    }
    return body


def _struct_node_id(work_id: str, address_path: str, structural_kind: str) -> str:
    identity = {
        "identity_schema": ADDRESS_ID_SCHEMA,
        "work_id": work_id,
        "structural_kind": structural_kind,
        "address_path": address_path,
        "special": "",
        "creation_event_id": "",
        "local_discriminator": "",
        "jurisdiction_profile_id": ADDRESS_PROFILE_ID,
    }
    return leaf_hash(_DOMAIN_ADDRESS_NODE, identity)


def _content_leaf_body(text: str) -> tuple[str, dict[str, JsonValue]]:
    """``lawvm.content_leaf.v1`` — PURE text identity (map §2; OBJECT_MODEL §4.4).

    The shared content leaf is the highest dedup anchor (design §22.1 anchor
    ladder: ``content_leaf_hash`` = "same text content wherever reused"). Its
    object body is therefore ``{schema, text, content_leaf_hash}`` and NOTHING
    per-work — no ``source_locators``, no ``work_id``. Identical leaf text in two
    different works produces a byte-identical object (and thus a byte-identical
    wrapped-row ``object_hash``), so it deduplicates at the
    ``content_leaf_root`` / shared-store level.

    ``content_leaf_hash = "sha256:" + sha256(canonical_json_bytes({schema,
    text}))`` (text NFC-normalized). Per-occurrence provenance (which work, which
    source span) rides on the ``node_version`` instead (OBJECT_MODEL §4.7) — the
    per-work-per-occurrence object — never on this shared leaf.
    """
    normalized = nfc(text)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CONTENT_LEAF,
        "text": normalized,
    }
    content_leaf_hash = leaf_hash(_DOMAIN_CONTENT_LEAF, _without(body, "content_leaf_hash"))
    body["content_leaf_hash"] = content_leaf_hash
    return content_leaf_hash, body


def _node_version_body(
    struct_node_id: str,
    content_leaf_hash: str,
    effective_interval: tuple[str, str | None],
    rail: str,
    produced_by_transition_id: str,
    source_locators: list[JsonValue],
) -> tuple[str, dict[str, JsonValue]]:
    """``lawvm.node_version.v1`` (OBJECT_MODEL §4.7).

    ``node_version_id = LeafHash("node_version", {struct_node_id,
    produced_by_transition_id, content_leaf_hash, effective_interval, branch_id,
    rail})``.

    ``source_locators`` are the per-work-per-occurrence source spans. They live
    HERE (the per-occurrence object), not on the shared content leaf, so the
    content leaf stays pure text and deduplicates across works (design §22.1).
    They are NOT a member of the ``node_version_id`` identity tuple (the §4.7
    formula above) — provenance does not perturb version identity — but they ARE
    a visible member of the emitted body (and so of the wrapped-row
    ``object_hash``), keeping the per-occurrence source binding committed.
    """
    identity: dict[str, JsonValue] = {
        "schema": SCHEMA_NODE_VERSION,
        "struct_node_id": struct_node_id,
        "produced_by_transition_id": produced_by_transition_id,
        "content_leaf_hash": content_leaf_hash,
        "effective_interval": [effective_interval[0], effective_interval[1]],
        "branch_id": _BRANCH_ID,
        "rail": rail,
    }
    node_version_id = leaf_hash(_DOMAIN_NODE_VERSION, identity)
    body = dict(identity)
    body["node_version_id"] = node_version_id
    body["source_locators"] = list(source_locators)
    return node_version_id, body


def _checkpoint_body(date: str, tree_hash: str, active_node_count: int) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CHECKPOINT,
        "effective_date": date,
        "tree_hash": _wrap_to_prefixed(tree_hash),
        "active_node_count": active_node_count,
    }
    return body


def _transition_body(
    sequence: int,
    effective_date: str,
    action: str,
    target_address: str,
    pre_hash: str,
    post_hash: str,
    payload_hash: str,
    source_refs: list[str],
) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_TRANSITION,
        "transition_id": f"t{sequence:06d}:{effective_date}:{target_address}",
        "sequence": sequence,
        "effective_date": effective_date,
        "action": action,
        "target_address": target_address,
        "pre_hash": _wrap_to_prefixed(pre_hash) if pre_hash else "",
        "post_hash": _wrap_to_prefixed(post_hash) if post_hash else "",
        "payload_hash": _wrap_to_prefixed(payload_hash) if payload_hash else "",
        "source_refs": list(source_refs),
        "source_anchors": [],
    }
    return body


def _without(body: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    return {k: v for k, v in body.items() if k != key}


def _work_source_ref(jurisdiction: str, canonical_id: str) -> str:
    """The opaque farchive locator string for a work/amending-act canonical id.

    Same shape the genesis source_ref uses (``farchive:<jur>:work:<canon>``) so a
    transition attributing to an amending act and one attributing to the base work
    carry locators of one form; the canonical id distinguishes which act.
    """
    return f"farchive:{jurisdiction}:work:{canonical_id}"


def _op_source_for_act(lo_ops: list[Any], profile: Any, canonical_id: str) -> Any:
    """Return the first ``op.source`` whose canonical statute id == ``canonical_id``.

    Used to recover the amending act's metadata (title/enacted/effective) once an
    act has been attributed. Returns ``None`` when no op carries it (the caller
    then emits typed-absent metadata — honest, never fabricated).
    """
    for op in lo_ops:
        src = op.source
        if src is None or not src.statute_id:
            continue
        if profile.canonical_statute_id(src.statute_id) == canonical_id:
            return src
    return None


def _source_artifact_body(
    *,
    source_id: str,
    kind: str,
    title: str,
    url: str,
    content_hash: str,
    date: str,
) -> dict[str, JsonValue]:
    """``lawvm.source_artifact.v1`` — one amending act / base statute (map: mirrors
    the old ``source_artifacts`` table).

    Fields mirror the dense exporter's ``SourceArtifactRow`` (``source_id`` =
    canonical global id, ``kind`` ∈ {statute, amendment}, ``title``, ``url``,
    ``content_hash``, ``date``). HONEST ABSENCE: a missing title/url/hash is the
    empty string the engine actually provided — never a fabricated value. The
    object is content-addressed: two distinct acts (distinct ``source_id``) yield
    distinct objects; the same act referenced from many transitions emits ONE.
    """
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_SOURCE_ARTIFACT,
        "source_id": source_id,
        "kind": kind,
        "canonical_id": source_id,
        "title": nfc(title),
        "url": url,
        "content_hash": content_hash,
        "date": date,
    }
    body["source_artifact_id"] = leaf_hash(
        _DOMAIN_SOURCE_ARTIFACT, _without(body, "source_artifact_id")
    )
    return body


def _residual_body(
    kind: str,
    blocking: bool,
    detail: str,
    subject: str,
    detail_fields: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """``lawvm.residual.v1`` — a single source/op/finding residual.

    ``detail_fields`` carries the source object's FULL distinguishing identity
    (Q4 fix): for a source pathology this is its ``as_detail()`` (``code``,
    ``amendment_id``, ``phase``, ``strict_disposition``, ``target_unit_kind``,
    …). Carrying it keeps DISTINCT engine pathologies DISTINCT content-addressed
    objects — without it, the message-only projection collapsed thousands of
    pathologies (different ``amendment_id``) to identical bodies that the proof
    SetRoot then silently deduped away.
    """
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_RESIDUAL,
        "kind": kind,
        "blocking": blocking,
        "detail": nfc(detail),
        "subject": subject,
        "detail_fields": _nfc_detail_fields(detail_fields or {}),
    }
    body["residual_id"] = leaf_hash(_DOMAIN_RESIDUAL, _without(body, "residual_id"))
    return body


def _nfc_detail_fields(fields: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """JSON-safe, deterministically-ordered copy of a residual's detail fields.

    String values are NFC-normalized (semantic-text identity discipline); other
    JSON scalars pass through; anything non-JSON is stringified so the residual
    body always serializes under ``canonical_json_bytes``.
    """
    out: dict[str, JsonValue] = {}
    for key in sorted(fields):
        val = fields[key]
        if isinstance(val, str):
            out[key] = nfc(val)
        elif isinstance(val, (bool, int, float)) or val is None:
            out[key] = val
        else:
            out[key] = nfc(str(val))
    return out


def _coverage_body(coverage_class: str, count: int, detail: str) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_COVERAGE,
        "coverage_class": coverage_class,
        "count": count,
        "detail": detail,
    }
    body["coverage_row_id"] = leaf_hash(_DOMAIN_COVERAGE, _without(body, "coverage_row_id"))
    return body


# --------------------------------------------------------------------------- #
# JSONL streaming writer                                                        #
# --------------------------------------------------------------------------- #


class _LayerWriter:
    """Streams ``{object_hash, object}`` rows to a layer JSONL file.

    Accumulates only the object hashes (for the SetRoot/SeqRoot) and the running
    uncompressed-byte sha256 — NEVER the full row bodies — so Rikoslaki streams
    without holding all dates in memory.
    """

    def __init__(self, path: Path, root_fn: str) -> None:
        self.path = path
        self.root_fn = root_fn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")
        self._hashes: list[str] = []
        self._seen: set[str] = set()
        self._byte_hasher = hashlib.sha256()
        self.row_count = 0

    def write(self, body: dict[str, JsonValue]) -> str:
        """Wrap a body, write the row, accumulate its hash. Returns object_hash.

        Set-rooted layers (``SetRoot``) forbid duplicate leaves (roots.py §2.1):
        two structurally identical objects (e.g. two source pathologies with the
        same detail) ARE the same content-addressed object, so a repeat is
        deduped — written once, its hash counted once. Sequence-rooted layers
        (``SeqRoot``, the trace layer) keep every row: transitions carry unique
        ids and order is significant, so a collision there is a real producer bug
        and is left to surface as a ``RootError``.
        """
        row = wrap_row(body)
        object_hash = str(row["object_hash"])
        if self.root_fn == "SetRoot" and object_hash in self._seen:
            return object_hash
        # Serialize the wrapped row deterministically (the on-disk transport form).
        line = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        encoded = (line + "\n").encode("utf-8")
        self._fh.write(line)
        self._fh.write("\n")
        self._byte_hasher.update(encoded)
        self._hashes.append(object_hash)
        self._seen.add(object_hash)
        self.row_count += 1
        return object_hash

    @property
    def hashes(self) -> list[str]:
        return self._hashes

    def root(self, domain: str) -> str:
        if self.root_fn == "SeqRoot":
            return seq_root(domain, self._hashes)
        return set_root(domain, self._hashes)

    def uncompressed_sha256(self) -> str:
        return "sha256:" + self._byte_hasher.hexdigest()

    def close(self) -> None:
        self._fh.close()


# --------------------------------------------------------------------------- #
# Per-address interval accumulation (the sparse core)                          #
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _OpenVersion:
    """A live (address, content) span being extended across change-dates."""

    structural_hash: str  # bare hex (transition/checkpoint lineage)
    content_leaf_hash: str  # "sha256:"-prefixed (text identity)
    effective: str
    rail: str
    node: IRNode


@dataclass
class ExporterResult:
    """Summary of an emitted pack (the CLI prints this)."""

    work_id: str
    out_dir: str
    pack_id: str
    n_change_dates: int
    n_content_leaves: int
    n_node_versions: int
    n_selection_rows: int
    n_transitions: int
    n_checkpoints: int
    n_address_nodes: int
    n_residuals: int
    n_source_artifacts: int
    leaf_dedup_attempts: int


# --------------------------------------------------------------------------- #
# The exporter                                                                  #
# --------------------------------------------------------------------------- #


def export_work_pack(
    work_id_input: str,
    out_dir: str | Path,
    *,
    jurisdiction: str = "fi",
    slice_prefix: str = "",
    granularity: str = "subsection",
    quiet: bool = False,
) -> ExporterResult:
    """Replay one work and emit its sparse certified pack under ``out_dir``.

    Mirrors ``export_transition_graph``'s replay path (same jurisdiction adapter,
    same per-date materialization), but emits the substrate object model instead
    of the dense SQLite db.
    """
    from lawvm.tools.export_transition_graph import (
        _index_ops_by_date,
        _index_ops_by_expiry_date,
        _ops_for_covering,
        covering_units,
        reproducible_tree_hash,
        resolve_commencement_date,
    )
    from lawvm.tools.transition_graph_jurisdictions import (
        transition_graph_adapter_for_jurisdiction,
    )

    adapter = transition_graph_adapter_for_jurisdiction(jurisdiction)
    profile = adapter.profile
    canonical_id = profile.canonical_statute_id(work_id_input)
    engine_id = profile.engine_statute_id(canonical_id)
    work_id = f"{jurisdiction}:act:{canonical_id}"

    if not quiet:
        print(f"[pack-work] replaying {engine_id} (engine authority)...", flush=True)
    import dataclasses as _dc

    bundle = _dc.replace(
        adapter.replay_runner(engine_id, profile=profile),
        statute_id=canonical_id,
        engine_id=engine_id,
    )
    materialize_tree = adapter.tree_materializer
    if not quiet:
        print(
            f"[pack-work] replay done: {len(bundle.change_dates)} change-dates, "
            f"{len(bundle.timelines)} timelines",
            flush=True,
        )

    # Per-transition amending-act attribution (the same engine signal the dense
    # ``export_transition_graph`` uses for ``source_artifacts``): each L2 op
    # carries its amending act on ``op.source.statute_id`` + the effective/expiry
    # date it lands on. We index the ops by effective date and by expiry date, then
    # — for each L3 transition (date, covering address) — find the op(s) whose
    # resolved target covers that address (``_ops_for_covering``). Their canonical
    # source ids ARE the amending acts that made the change. Genesis/commencement
    # transitions correctly attribute to the base work (no amending op there).
    ops_by_date = _index_ops_by_date(bundle.lo_ops)
    expiry_ops_by_date = _index_ops_by_expiry_date(bundle.lo_ops)

    def _attributed_acts(date: str, addr: str) -> list[str]:
        """Distinct canonical amending-act ids attributing this (date, addr) change.

        Reuses the dense exporter's covering-attribution. Returns the sorted
        distinct canonical statute ids of every op (effective OR fixed-term expiry
        on ``date``) whose resolved target covers ``addr``, EXCLUDING the base work
        itself (a base-work-internal op is the original enactment, not an
        amendment). Empty when no amending act is identifiable — the caller then
        attributes honestly to the base work / genesis, never fabricating an act.
        """
        ops = _ops_for_covering(ops_by_date.get(date, []), addr)
        expiring = _ops_for_covering(expiry_ops_by_date.get(date, []), addr)
        acts: set[str] = set()
        for op in (*ops, *expiring):
            src = op.source
            if src is None or not src.statute_id:
                continue
            canon = profile.canonical_statute_id(src.statute_id)
            if canon and canon != canonical_id:
                acts.add(canon)
        return sorted(acts)

    # ``corpus_version`` is a HASHED manifest member and flows into every
    # selection-row/fact ``account_interval`` + the ``account_boundary_root``, so
    # it MUST be derived deterministically from the engine INPUT — NOT from
    # ``date.today()`` (a wall-clock value made the same input produce a different
    # ``pack_id`` on a different day, defeating content-addressing and third-party
    # reproduction). The wall-clock republish moment lives only on the
    # hash-EXCLUDED ``provenance.created_at``. The digest below pins the work's
    # temporal/identity skeleton (work_id, title, commencement, the ordered
    # change-date frontier, whose first entry is the commencement); the actual
    # per-date tree CONTENT is independently content-addressed by the layer roots
    # that also flow into ``pack_id``.
    corpus_version = _corpus_version(
        jurisdiction=jurisdiction,
        work_id=work_id,
        title=bundle.title,
        change_dates=bundle.change_dates,
    )
    out = Path(out_dir)
    if out.exists():
        # Idempotent: clear a previous pack at this path.
        import shutil

        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # -- open layer writers -------------------------------------------------- #
    writers: dict[str, _LayerWriter] = {}
    for kind, fname, root_fn in _FILLED_LAYERS:
        writers[kind] = _LayerWriter(out / fname, root_fn)
    for reserved in _RESERVED_DIRS:
        (out / reserved).mkdir(parents=True, exist_ok=True)

    base_w = writers["base"]
    state_w = writers["state"]
    trace_w = writers["trace"]
    proof_w = writers["proof"]

    # -- domain-tagged sub-collections for the multi-root --------------------- #
    content_leaf_hashes: list[str] = []  # object_hash of each emitted content_leaf
    node_version_hashes: list[str] = []
    selection_profile_hashes: list[str] = []
    selection_universe_hashes: list[str] = []
    scope_predicate_hashes: list[str] = []
    applicability_fact_hashes: list[str] = []
    candidate_set_hashes: list[str] = []
    selection_row_hashes: list[str] = []

    # -- work + selection-profile + scope-predicate (the fixed preamble) ------ #
    work_body = _work_body(work_id, bundle.title, jurisdiction, corpus_version)
    base_w.write(work_body)
    n_address_nodes = 0

    # One closed total scope predicate for v0 (no scope dimensions narrowed).
    total_scope = ScopePredicate(dimensions={}, scope_status="total")
    scope_predicate_id = total_scope.scope_predicate_id
    h = state_w.write(total_scope.to_canonical_dict())
    scope_predicate_hashes.append(h)

    # The three pinned selection profiles (the checker reads selection rows; only
    # governing_text is used to produce rows in v0, but all three are committed).
    governing_profile = None
    for prof in v0_profiles():
        h = state_w.write(prof.to_canonical_dict())
        selection_profile_hashes.append(h)
        if prof.profile_id == _PROFILE_ID:
            governing_profile = prof
    assert governing_profile is not None

    # v0 minimal source ref: an opaque farchive locator string for the work.
    source_ref = f"farchive:{jurisdiction}:work:{canonical_id}"

    # -- genesis: InitialStateEvent ------------------------------------------- #
    commencement = resolve_commencement_date(bundle.timelines, profile=profile) or (
        bundle.change_dates[0] if bundle.change_dates else "0001-01-01"
    )
    genesis = InitialStateEvent(
        work_id=work_id,
        genesis_kind="original_enactment",
        effective_date=commencement,
        prior_history_status="none",
        source_refs=(source_ref,),
        creation_event_id=None,
    )
    base_w.write(genesis.to_canonical_dict())

    # -- per-date materialization (streaming) --------------------------------- #
    # ``open_versions`` maps address -> the live span being extended. When an
    # address's structural hash changes (or it disappears), we CLOSE the span at
    # the new date (exclusive end), emit its node_version + selection row, then
    # open a fresh span. This yields MAXIMAL constant intervals — sparse, never
    # one row per date (map §8 gotcha 3).
    open_versions: dict[str, _OpenVersion] = {}
    emitted_content_leaves: dict[str, str] = {}  # content_leaf_hash -> object_hash
    address_nodes_seen: set[str] = set()
    address_struct_kind: dict[str, str] = {}
    leaf_dedup_attempts = 0
    seq = 0
    n_transitions = 0
    n_checkpoints = 0
    n_node_versions = 0
    n_selection_rows = 0
    expected_selection_keys: dict[str, str] = {}

    # Universe boundary roots (the address/effect/account/scope structural keys
    # the universe commits to — used so the universe object is well-formed).
    all_addresses: set[str] = set()
    all_effect_dates: set[str] = set()

    # Distinct amending acts attributed across all transitions (canonical id ->
    # the OperationSource we first saw for it, carrying title/enacted/effective).
    # Drives the ``lawvm.source_artifact.v1`` objects emitted below.
    attributed_act_sources: dict[str, Any] = {}

    def _ensure_content_leaf(node: IRNode) -> str:
        """Emit a content leaf (text-only) if unseen; return its object_hash."""
        nonlocal leaf_dedup_attempts
        leaf_dedup_attempts += 1
        text = irnode_to_text(node)
        clh, body = _content_leaf_body(text)
        existing = emitted_content_leaves.get(clh)
        if existing is not None:
            return existing
        object_hash = base_w.write(body)
        emitted_content_leaves[clh] = object_hash
        content_leaf_hashes.append(object_hash)
        return object_hash

    def _ensure_address_node(addr: str, node: IRNode) -> str:
        nonlocal n_address_nodes
        if addr not in address_nodes_seen:
            address_nodes_seen.add(addr)
            structural_kind = str(node.kind)
            address_struct_kind[addr] = structural_kind
            base_w.write(_address_node_body(work_id, addr, structural_kind))
            n_address_nodes += 1
        return _struct_node_id(work_id, addr, address_struct_kind[addr])

    def _close_span(addr: str, span: _OpenVersion, end_date: str | None) -> None:
        """Emit node_version + applicability_fact + candidate_set + selection_row."""
        nonlocal n_node_versions, n_selection_rows
        struct_id = _struct_node_id(work_id, addr, address_struct_kind[addr])
        # Ensure the content leaf is present (dedup).
        _ensure_content_leaf(span.node)
        clh = span.content_leaf_hash
        produced_by = f"genesis:{addr}" if span.effective == commencement else f"t:{addr}:{span.effective}"
        nv_id, nv_body = _node_version_body(
            struct_id,
            clh,
            _interval(span.effective, end_date),
            span.rail,
            produced_by,
            [source_ref],
        )
        h = state_w.write(nv_body)
        node_version_hashes.append(h)
        n_node_versions += 1

        # applicability_fact — the audited basis the L1 checker verifies against.
        fact = ApplicabilityFact(
            work_id=work_id,
            address_id=struct_id,
            node_version_id=nv_id,
            content_leaf_hash=clh,
            branch_id=_BRANCH_ID,
            effect_interval=_interval(span.effective, end_date),
            enactment_interval=_interval(span.effective, end_date),
            account_interval=(corpus_version, None),
            rail=span.rail,
            scope_predicate_id=scope_predicate_id,
            precedence_class="same_rail_latest",
            temporal_basis=TemporalBasis(kind="fixed_date"),
            produced_by_transition_id=produced_by,
        )
        h = state_w.write(fact.to_canonical_dict())
        applicability_fact_hashes.append(h)

        # candidate_set — single eligible candidate (the selected version), complete.
        cand = SelectionCandidate(
            node_version_id=nv_id,
            rail=span.rail,
            effect_interval=_interval(span.effective, end_date),
            scope_predicate_id=scope_predicate_id,
            eligible=True,
        )
        cset = SelectionCandidateSet(
            selection_key=f"{struct_id}:{span.effective}",
            candidates=(cand,),
            complete=True,
        )
        cs_object_hash = state_w.write(cset.to_canonical_dict())
        candidate_set_hashes.append(cs_object_hash)

        # selection_row — the sparse public answer (SELECTED over the interval).
        row = SelectionRow(
            work_id=work_id,
            query_profile_id=_PROFILE_ID,
            branch_id=_BRANCH_ID,
            address_id=struct_id,
            scope_query_id=scope_predicate_id,
            effect_interval=_interval(span.effective, end_date),
            account_interval=(corpus_version, None),
            source_policy_id="archival_exact",
            selection_status="selected",
            candidate_set_hash=cs_object_hash,
            selected_node_version_id=nv_id,
            decision_basis=DecisionBasis(
                selection_rule_id=_PROFILE_ID,
                applicability_fact_refs=(fact.fact_id,),
            ),
        )
        # The on-disk selection_row body carries ``selection_key`` as an EXPLICIT
        # field (the checker's L0.6 universe-domain check reads body["selection_key"];
        # the substrate ``to_canonical_dict`` keeps it a computed property, so it is
        # injected here). The key is computed over the property's hashed body
        # (without the key), then carried in the emitted body — both the universe
        # map key and present-row key resolve to the same string.
        selection_key = row.selection_key
        row_body = row.to_canonical_dict()
        row_body["selection_key"] = selection_key
        row_object_hash = state_w.write(row_body)
        selection_row_hashes.append(row_object_hash)
        expected_selection_keys[selection_key] = row_object_hash
        n_selection_rows += 1

    prev_struct: dict[str, str] = {}

    for date in bundle.change_dates:
        all_effect_dates.add(date)
        tree = materialize_tree(bundle, date)
        units = covering_units(tree, slice_prefix, granularity)
        cur_struct: dict[str, str] = {}
        cur_nodes: dict[str, IRNode] = {}
        for addr, node in units:
            all_addresses.add(addr)
            sh = structural_subtree_hash(node)
            cur_struct[addr] = sh  # last-in-document-order wins (dedup by address)
            cur_nodes[addr] = node

        # Checkpoint over the deduped covering set (structural hashes).
        tree_hash = reproducible_tree_hash(list(cur_struct.items()))
        trace_w.write(_checkpoint_body(date, tree_hash, len(cur_struct)))
        n_checkpoints += 1

        # Diff prev -> cur: close spans that changed/disappeared, emit transitions.
        all_addrs = list(dict.fromkeys(list(prev_struct.keys()) + list(cur_struct.keys())))
        for addr in all_addrs:
            pre = prev_struct.get(addr, "")
            post = cur_struct.get(addr, "")
            if pre == post:
                continue
            seq += 1
            if pre == "" and post != "":
                action = "set_subtree"
            elif pre != "" and post == "":
                action = "delete_subtree"
            else:
                action = "set_subtree"
            payload_hash = post if action == "set_subtree" else ""

            # Attribute this transition to the REAL amending act(s) that made the
            # change. A genesis/commencement transition (or any change with no
            # identifiable amending op) attributes honestly to the base work via
            # ``source_ref``; amendments attribute to their amending act's locator.
            acts = _attributed_acts(date, addr)
            if acts:
                tx_source_refs = [_work_source_ref(jurisdiction, a) for a in acts]
                for canon in acts:
                    if canon not in attributed_act_sources:
                        attributed_act_sources[canon] = _op_source_for_act(
                            bundle.lo_ops, profile, canon
                        )
            else:
                tx_source_refs = [source_ref]
            trace_w.write(
                _transition_body(seq, date, action, addr, pre, post, payload_hash, tx_source_refs)
            )
            n_transitions += 1

            # Close the open span for this address at `date` (exclusive end).
            if addr in open_versions:
                _close_span(addr, open_versions.pop(addr), date)

            # Open a fresh span if the address is now present.
            if post != "":
                node = cur_nodes[addr]
                _ensure_address_node(addr, node)
                clh, _ = _content_leaf_body(irnode_to_text(node))
                rail = _version_rail(bundle, addr, date)
                open_versions[addr] = _OpenVersion(
                    structural_hash=post,
                    content_leaf_hash=clh,
                    effective=date,
                    rail=rail,
                    node=node,
                )

        prev_struct = cur_struct

    # Close any still-open spans at the open end (None = +infinity).
    for addr in list(open_versions.keys()):
        _close_span(addr, open_versions.pop(addr), _OPEN_END)

    # -- source_artifact objects (the amending-act metadata) ------------------ #
    # One ``lawvm.source_artifact.v1`` per distinct act a transition attributes to,
    # mirroring the dense exporter's ``source_artifacts`` table: the base statute
    # (kind=statute) plus every distinct amending act (kind=amendment) referenced
    # by a transition's ``source_refs``. The viewer reads these to show act titles
    # ("§X amended by act Y on date D"). The base statute's locator
    # (``farchive:<jur>:work:<canonical>``) is exactly the genesis/base ``source_ref``
    # the genesis-attributed transitions carry, so each transition's ``source_refs``
    # resolve to a source_artifact object. Emitted into the base layer (SetRoot —
    # the same act referenced N times is ONE content-addressed object).
    statute_url = profile.statute_url(canonical_id, engine_id) if hasattr(profile, "statute_url") else ""
    base_w.write(
        _source_artifact_body(
            source_id=canonical_id,
            kind="statute",
            title=bundle.title,
            url=statute_url,
            content_hash="",
            date="",
        )
    )
    n_source_artifacts = 1
    for canon in sorted(attributed_act_sources):
        src = attributed_act_sources[canon]
        title = (getattr(src, "title", "") or "") if src is not None else ""
        date = ""
        if src is not None:
            date = getattr(src, "enacted", "") or getattr(src, "effective", "") or ""
        engine_amd = profile.engine_statute_id(canon)
        url = profile.amendment_url(canon, engine_amd) if hasattr(profile, "amendment_url") else ""
        base_w.write(
            _source_artifact_body(
                source_id=canon,
                kind="amendment",
                title=title,
                url=url,
                content_hash="",
                date=date,
            )
        )
        n_source_artifacts += 1

    # -- residuals / coverage (proof layer) ----------------------------------- #
    # Each residual carries the source object's FULL distinguishing identity
    # (``detail_fields`` = its ``as_detail()``), so DISTINCT engine pathologies /
    # findings stay DISTINCT content-addressed objects and are NOT silently
    # deduped by the proof SetRoot (Q4). ``blocking`` is read from the object's
    # own field (never hardcoded), so a blocking source-pathology/finding cannot
    # be demoted out of the certification fold. ``emitted_residuals`` counts the
    # objects ACTUALLY emitted (post-dedup) so the coverage ``residual`` row can
    # NOT diverge from the number of residual objects on disk.
    proof_residuals_before = proof_w.row_count

    def _emit_residual(
        kind: str, subject: str, blocking: bool, detail: str, detail_fields: dict[str, JsonValue]
    ) -> None:
        proof_w.write(
            _residual_body(kind, blocking, detail, subject, detail_fields=detail_fields)
        )

    for path in getattr(bundle, "source_pathologies", []) or []:
        fields = _detail_fields(path)
        _emit_residual(
            "source_pathology",
            "source",
            _detail_blocking(path, fields),
            _residual_detail(path, fields),
            fields,
        )
    for failed in getattr(bundle, "failed_ops", []) or []:
        fields = _detail_fields(failed)
        # A failed_op is a REJECTED legal operation — inherently blocking unless
        # its own detail explicitly says otherwise.
        _emit_residual(
            "failed_operation",
            "op",
            _detail_blocking(failed, fields, default=True),
            _residual_detail(failed, fields),
            fields,
        )
    # Q1: replay_findings (blocking/violation roles) were silently dropped — emit
    # one residual per finding so a blocking replay finding cannot vanish without
    # a trace in the proof layer / certification fold.
    for finding in getattr(bundle, "replay_findings", []) or []:
        fields = _finding_fields(finding)
        _emit_residual(
            "replay_finding",
            "replay",
            _finding_blocking(finding),
            _finding_detail(finding, fields),
            fields,
        )

    # The number of residual OBJECTS on disk (post-SetRoot-dedup) — the coverage
    # row reports exactly this, so the count can never diverge from reality.
    n_residuals = proof_w.row_count - proof_residuals_before

    proof_w.write(
        _coverage_body("owned", n_selection_rows, "selected selection rows over covering frontier")
    )
    proof_w.write(
        _coverage_body("residual", n_residuals, "distinct source/op/finding residual objects")
    )

    # -- selection universe (the omission keystone) --------------------------- #
    universe = SelectionUniverse(
        work_id=work_id,
        query_profile_ids=(_PROFILE_ID,),
        branch_ids=(_BRANCH_ID,),
        expected_selection_keys=expected_selection_keys,
        address_root=set_root(
            "address_universe", [leaf_hash("addr", a) for a in sorted(all_addresses)]
        ),
        effect_boundary_root=set_root(
            "effect_boundary", [leaf_hash("effect", d) for d in sorted(all_effect_dates)]
        ),
        account_boundary_root=set_root("account_boundary", [leaf_hash("account", corpus_version)]),
        scope_query_root=set_root("scope_query", [scope_predicate_id]),
    )
    universe_object_hash = state_w.write(universe.to_canonical_dict())
    selection_universe_hashes.append(universe_object_hash)

    # -- edges/ layer: the body cross-reference relation graph ---------------- #
    # The FI body cross-references become REAL ``lawvm.legal_relation_edge.v0``
    # rows so the checker's L0.8 authority-legality matrix exercises live data
    # (design §25.4 — extraction, not greenfield). The edges are a SET (an
    # additive overlay family — the omission keystone already committed to its
    # absence via the reserved dir). If a work has no resolvable body references
    # the layer is absent (no fabrication). Only FI carries the extractor; other
    # jurisdictions emit no edges (their reserved ``edges/`` dir stays empty).
    edge_bodies: list[dict[str, JsonValue]] = []
    if jurisdiction == "fi":
        edge_bodies = _build_fi_relation_edges(
            engine_id=engine_id,
            corpus_version=corpus_version,
        )
        # Additive EU directive mini-vertical (design §25.8): where the act's own
        # prose CLAIMS to transpose an EU directive, also emit the deterministic,
        # verifiable edges — claimed-transposition + timeliness (deadline seed vs
        # this work's commencement) + an honest "conformance not assessed"
        # residual. A work with NO transposition claim emits nothing new. NEVER a
        # substantive conformance / direct-effect / breach conclusion.
        edge_bodies.extend(
            _build_fi_transposition_edges(
                engine_id=engine_id,
                corpus_version=corpus_version,
                commencement_date=commencement,
            )
        )
    edges_layer: PackLayer | None = None
    if edge_bodies:
        edges_rel = f"edges/{corpus_version}/edges.jsonl"
        edges_path = out / edges_rel
        edges_path.parent.mkdir(parents=True, exist_ok=True)
        edges_hashes: list[str] = []
        edges_seen: set[str] = set()
        edges_byte_hasher = hashlib.sha256()
        with edges_path.open("w", encoding="utf-8") as fh:
            for body in edge_bodies:
                row = wrap_row(body)
                object_hash = str(row["object_hash"])
                # SetRoot semantics: a duplicate edge (same content) is the same
                # content-addressed object — written once, counted once.
                if object_hash in edges_seen:
                    continue
                edges_seen.add(object_hash)
                line = json.dumps(
                    row, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                fh.write(line)
                fh.write("\n")
                edges_byte_hasher.update((line + "\n").encode("utf-8"))
                edges_hashes.append(object_hash)
        edges_root = set_root(_DOMAIN_EDGES, edges_hashes)
        edges_uncompressed = "sha256:" + edges_byte_hasher.hexdigest()
        edges_layer = PackLayer(
            kind="edges",
            path=edges_rel,
            row_schema=SCHEMA_RELATION_EDGE,
            codec=STORAGE_CODEC,
            dict_id="",
            uncompressed_sha256=edges_uncompressed,
            storage_sha256=edges_uncompressed,
            root=edges_root,
            root_fn="SetRoot",
            row_count=len(edges_hashes),
        )

    # -- close writers, compute roots ----------------------------------------- #
    for w in writers.values():
        w.close()

    # The roots-of-roots map is built by the SAME function the checker recomputes
    # over the loaded rows (``checker.assemble_manifest_roots``) — one algorithm,
    # so a checker re-derivation cannot disagree with an honest exporter, and a
    # forged ``manifest.roots`` map is rejected (FIX-1). The trace layer is a
    # SeqRoot (``materialization_root`` over its ordered rows); everything else is
    # grouped by object family.
    roots = assemble_manifest_roots(
        content_leaf_hashes=content_leaf_hashes,
        node_version_hashes=node_version_hashes,
        selection_profile_hashes=selection_profile_hashes,
        selection_universe_hashes=selection_universe_hashes,
        scope_predicate_hashes=scope_predicate_hashes,
        applicability_fact_hashes=applicability_fact_hashes,
        candidate_set_hashes=candidate_set_hashes,
        selection_row_hashes=selection_row_hashes,
        trace_hashes=trace_w.hashes,
        source_refs=(source_ref,),
    )
    materialization_root = roots["materialization_root"]
    selection_index_root = roots["selection_index_root"]
    certificate_root = roots["certificate_root"]

    # -- certificate (cert/ singleton) ---------------------------------------- #
    # The cert body restates the legal-state roots it commits to; its
    # ``certificate_root`` MUST equal the shared map's (asserted below).
    emitted_cert_root, cert_body = _build_certificate(
        work_id=work_id,
        materialization_root=materialization_root,
        selection_index_root=selection_index_root,
        n_residuals=n_residuals,
    )
    assert emitted_cert_root == certificate_root, (
        "certificate_root drift between _build_certificate and assemble_manifest_roots"
    )
    cert_dir = out / "cert"
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / "certificate.json").write_text(
        json.dumps(wrap_row(cert_body), ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    # -- layer descriptors + manifest ----------------------------------------- #
    layers = _build_layer_descriptors(writers)
    if edges_layer is not None:
        layers = (*layers, edges_layer)

    schemas = {
        "work": SCHEMA_WORK,
        "address_node": SCHEMA_ADDRESS_NODE,
        "content_leaf": SCHEMA_CONTENT_LEAF,
        "node_version": SCHEMA_NODE_VERSION,
        "selection_row": "lawvm.selection_row.v1",
        "applicability_fact": "lawvm.applicability_fact.v1",
        "certified_tree_transition": SCHEMA_TRANSITION,
        "checkpoint": SCHEMA_CHECKPOINT,
        "source_artifact": SCHEMA_SOURCE_ARTIFACT,
    }
    if edges_layer is not None:
        schemas["relation_edge"] = SCHEMA_RELATION_EDGE

    provenance = PackProvenance(
        lawvm_git_commit=_git_commit(),
        engine_version="lawvm.engine.replay",
        source_policy_id="archival_exact",
        checkable_source_bundle_policy="archival_exact",
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        dirty_tree=False,
    )

    manifest = PackManifest(
        pack_kind=PACK_KIND,
        work_ids=(work_id,),
        corpus_version=corpus_version,
        identity_encoding=IDENTITY_ENCODING,
        storage_codec=STORAGE_CODEC,
        dict_id="",
        profiles=(CANON_PROFILE,),
        selection_profiles=(_PROFILE_ID,),
        schemas=schemas,
        layers=layers,
        roots=roots,
        required_layers_for_browse=("base", "state", "cert"),
        required_layers_for_audit=("base", "state", "trace", "proof", "cert"),
        optional_layers=("surface", "edges", "branch", "overlay", "projection", "dict"),
        provenance=provenance,
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

    return ExporterResult(
        work_id=work_id,
        out_dir=str(out),
        pack_id=manifest.pack_id,
        n_change_dates=len(bundle.change_dates),
        n_content_leaves=len(content_leaf_hashes),
        n_node_versions=n_node_versions,
        n_selection_rows=n_selection_rows,
        n_transitions=n_transitions,
        n_checkpoints=n_checkpoints,
        n_address_nodes=n_address_nodes,
        n_residuals=n_residuals,
        n_source_artifacts=n_source_artifacts,
        leaf_dedup_attempts=leaf_dedup_attempts,
    )


_DOMAIN_EDGES = "edges"


def _build_fi_relation_edges(
    *,
    engine_id: str,
    corpus_version: str,
) -> list[dict[str, JsonValue]]:
    """Extract FI body cross-references and bridge them to relation-edge bodies.

    Reuses the FI reference-extraction entrypoint READ-ONLY
    (``extract_all_reference_mentions`` — the same extractor ``lawvm refs`` /
    ``fi_refs.parquet`` project through), folds the flattened per-target mentions
    back into ONE reference SET per written surface (``fold_reference_set`` —
    so a range/coordination is ONE set, not N rows), and maps each folded set to
    a ``lawvm.legal_relation_edge.v0`` body (``reference_set_to_relation_edge``).

    Returns the list of edge bodies (possibly empty — a work with no resolvable
    references emits NO edges, never a fabricated one). FI-specific by
    construction (the extractor is Finland's); the caller guards on jurisdiction.
    """
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions
    from lawvm.finland.references.reference_sets import fold_reference_set
    from lawvm.substrate.relation_edge_bridge import reference_set_to_relation_edge

    store = get_corpus_store()
    try:
        # The FI corpus store keys consolidated oracle text by the ENGINE id
        # (year-major, e.g. "2004/301") — the same id form ``lawvm refs`` /
        # ``fi_refs`` iterate. The emitted ``ProvisionRef.statute_id`` therefore
        # carries the engine id; it is an opaque content-addressed target ref in
        # the edge, so the id flavour does not affect edge legality.
        xml_bytes = store.read_oracle(engine_id)
    except Exception:
        # No consolidated oracle text for this work → no body references to
        # extract. Honest absence (an empty edges layer), never a fabrication.
        return []
    if not xml_bytes:
        return []

    result = extract_all_reference_mentions(xml_bytes, engine_id)

    # Group flattened mentions by their written surface (surface_text + span):
    # the mentions of ONE range/coordination share these, so they fold into ONE
    # set. Metadata-derived mentions (no surface/span) are keyed by their own
    # identity so each stays a distinct singleton set (never merged by accident).
    groups: dict[tuple[str, JsonValue], list[Any]] = {}
    order: list[tuple[str, JsonValue]] = []
    for idx, mention in enumerate(result.mentions):
        span = mention.source_span
        if mention.surface_text and span is not None:
            key: tuple[str, JsonValue] = (
                mention.surface_text,
                f"{span.source_file}:{span.byte_offset}:{span.byte_len}",
            )
        else:
            # No shared surface anchor — keep this mention its own group so it is
            # neither merged with an unrelated mention nor silently dropped.
            key = ("\x00solo", idx)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(mention)

    edges: list[dict[str, JsonValue]] = []
    for key in order:
        folded = fold_reference_set(
            groups[key],
            corpus_version=corpus_version,
            branch=_BRANCH_ID,
        )
        edges.append(
            reference_set_to_relation_edge(
                expression=folded.expression,
                resolution=folded.resolution,
                corpus_version=corpus_version,
                branch_id=_BRANCH_ID,
            )
        )
    return edges


def _build_fi_transposition_edges(
    *,
    engine_id: str,
    corpus_version: str,
    commencement_date: str,
) -> list[dict[str, JsonValue]]:
    """Extract FI EU-directive transposition claims → relation-edge bodies (§25.8).

    Reuses the FI consolidated oracle text READ-ONLY (the same bytes
    :func:`_build_fi_relation_edges` reads) and the deterministic transposition
    extractor (``recognize_transposition_claims``), then bridges each claim to
    its EU directive edges (``transposition_claim_to_edges`` —
    ``source_claimed_transposition`` + ``timeliness_fact`` + a "conformance not
    assessed" residual). The timeliness edge compares the curated demo deadline
    seed against THIS work's ``commencement_date`` (from the work's replayed
    timeline, supplied by the caller).

    Returns the edge bodies (possibly empty — an act with no explicit
    transposition claim emits NONE, never a fabricated one). FI-specific by
    construction; the caller guards on jurisdiction. A substantive conformance /
    direct-effect / breach conclusion is NEVER emitted — only the deterministic
    evidentiary edges + the honest "not assessed" residual.
    """
    from lawvm.finland.corpus import get_corpus_store
    from lawvm.finland.references.eu_transposition import (
        recognize_transposition_claims,
    )
    from lawvm.substrate.eu_transposition_bridge import (
        transposition_claim_to_edges,
    )

    store = get_corpus_store()
    try:
        xml_bytes = store.read_oracle(engine_id)
    except Exception:
        # No consolidated oracle text → no prose to scan. Honest absence.
        return []
    if not xml_bytes:
        return []

    text = xml_bytes.decode("utf-8", "replace")
    claims = recognize_transposition_claims(text, citing_engine_id=engine_id)
    edges: list[dict[str, JsonValue]] = []
    for claim in claims:
        edges.extend(
            transposition_claim_to_edges(
                claim,
                commencement_date=commencement_date,
                corpus_version=corpus_version,
                branch_id=_BRANCH_ID,
            )
        )
    return edges


def _version_rail(bundle: Any, addr: str, date: str) -> str:
    """Best-effort rail: 'temporary' if a timeline version is fixed-term here."""
    # v0: default permanent. A richer rail derivation would consult the timeline
    # ProvisionVersion.variant_kind; the covering-unit address keying differs from
    # the timeline's LegalAddress keying, so v0 keeps the conservative default.
    return _RAIL_PERMANENT


def _detail_fields(obj: Any) -> dict[str, JsonValue]:
    """Return the source object's FULL ``as_detail()`` dict (Q4 distinguishing id).

    The engine pathology / failed-op objects expose ``as_detail()`` carrying the
    fields that make two otherwise-similar pathologies DISTINCT (``code``,
    ``amendment_id``, ``phase``, ``blocking``, ``strict_disposition``,
    ``target_unit_kind``, …). When an object lacks ``as_detail()`` we fall back to
    a single stringified body so it is still emitted (never silently dropped).
    """
    as_detail = getattr(obj, "as_detail", None)
    if callable(as_detail):
        detail = as_detail()
        if isinstance(detail, dict):
            return cast("dict[str, JsonValue]", detail)
    return {"repr": str(obj)}


def _detail_blocking(obj: Any, fields: dict[str, JsonValue], *, default: bool = False) -> bool:
    """Read the object's OWN ``blocking`` flag — never hardcode it.

    The flag lives on ``as_detail()["blocking"]`` (the engine pathology carries
    it there, not as a Python attribute), with the attribute as a secondary
    source. ``default`` is the value when neither is present (a rejected
    failed_op is inherently blocking).
    """
    if "blocking" in fields and isinstance(fields["blocking"], bool):
        return fields["blocking"]
    attr = getattr(obj, "blocking", None)
    if isinstance(attr, bool):
        return attr
    return default


def _residual_detail(obj: Any, fields: dict[str, JsonValue]) -> str:
    """Human-readable summary line for a residual (the structured fields carry id)."""
    for key in ("message", "reason", "detail", "code", "reason_code"):
        val = fields.get(key)
        if isinstance(val, str) and val:
            return val[:200]
    for attr in ("message", "detail", "kind", "reason"):
        val = getattr(obj, attr, None)
        if val:
            return str(val)[:200]
    return str(obj)[:200]


def _finding_fields(finding: Any) -> dict[str, JsonValue]:
    """Distinguishing identity for a replay finding (Q1)."""
    detail = getattr(finding, "detail", None)
    base: dict[str, JsonValue] = {}
    if isinstance(detail, dict):
        base = cast("dict[str, JsonValue]", dict(detail))
    for attr in ("kind", "role", "stage", "source_statute"):
        val = getattr(finding, attr, None)
        if val is not None and attr not in base:
            base[attr] = val if isinstance(val, (str, int, float, bool)) else str(val)
    base["blocking"] = bool(getattr(finding, "blocking", False))
    if not base:
        base = {"repr": str(finding)}
    return base


def _finding_blocking(finding: Any) -> bool:
    role = str(getattr(finding, "role", "") or "")
    return bool(getattr(finding, "blocking", False)) or role == "violation"


def _finding_detail(finding: Any, fields: dict[str, JsonValue]) -> str:
    kind = str(getattr(finding, "kind", "") or "")
    role = str(getattr(finding, "role", "") or "")
    summary = (kind + (f" [{role}]" if role else "")).strip()
    return summary[:200] if summary else str(finding)[:200]


def _build_certificate(
    *,
    work_id: str,
    materialization_root: str,
    selection_index_root: str,
    n_residuals: int,
) -> tuple[str, dict[str, JsonValue]]:
    """Build the v0 certificate singleton + its certificate_root.

    The certificate commits to the legal-state roots; ``certificate_root`` is a
    SetRoot over the committed subroots, consistent with the manifest ``roots``
    so a checker can pin it.
    """
    subroots = [materialization_root, selection_index_root]
    certificate_root = set_root("certificate", subroots)
    body: dict[str, JsonValue] = {
        "schema": SCHEMA_CERTIFICATE,
        "work_id": work_id,
        "materialization_root": materialization_root,
        "selection_index_root": selection_index_root,
        "certificate_root": certificate_root,
        "residual_count": n_residuals,
        "certification_status": "clean" if n_residuals == 0 else "qualified",
    }
    return certificate_root, body


def _build_layer_descriptors(
    writers: dict[str, _LayerWriter],
) -> tuple[PackLayer, ...]:
    """Build one PackLayer descriptor per filled layer.

    Each layer's ``root`` is the SetRoot/SeqRoot over its rows — exactly what the
    checker recomputes (L0.3). The domain tag per layer is fixed below.
    """
    # Domain used to root each layer's rows. The checker's L0.3 uses the layer's
    # declared ``domain`` (carried in PackLayerData at read time, derived here).
    layer_domain = {
        "base": "base",
        "state": "state",
        "trace": "trace",
        "proof": "proof",
    }
    descriptors: list[PackLayer] = []
    for kind in ("base", "state", "trace", "proof"):
        w = writers[kind]
        domain = layer_domain[kind]
        root = w.root(domain)
        fname = f"{kind}/{kind}.jsonl"
        descriptors.append(
            PackLayer(
                kind=kind,
                path=fname,
                row_schema=f"lawvm.layer.{kind}.v0",
                codec=STORAGE_CODEC,
                dict_id="",
                uncompressed_sha256=w.uncompressed_sha256(),
                storage_sha256=w.uncompressed_sha256(),
                root=root,
                root_fn=w.root_fn,
                row_count=w.row_count,
            )
        )
    return tuple(descriptors)


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


# --------------------------------------------------------------------------- #
# Pack reader (for the check-pack CLI) — reconstruct a checker Pack from disk   #
# --------------------------------------------------------------------------- #


def load_pack_for_check(pack_dir: str | Path) -> Any:
    """Read an on-disk pack back into an in-memory :class:`Pack` for the checker.

    Reconstructs the layer rows, the manifest, the selection universe map, and
    the referential-closure references the checker's L0 needs. Jurisdiction-
    neutral: reads ``schema`` / ``root_fn`` from the self-describing manifest.
    """
    from lawvm.substrate.checker import Pack, PackLayerData

    pack_path = Path(pack_dir)
    manifest_row = json.loads((pack_path / "manifest.json").read_text(encoding="utf-8"))
    manifest_body = manifest_row["object"] if "object" in manifest_row else manifest_row
    manifest = _manifest_from_body(manifest_body)

    layer_domain = {"base": "base", "state": "state", "trace": "trace", "proof": "proof"}
    layers: dict[str, PackLayerData] = {}
    for layer in manifest.layers:
        kind = layer.kind
        rows: list[dict[str, JsonValue]] = []
        layer_file = pack_path / layer.path
        if layer_file.exists():
            with layer_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rows.append(json.loads(line))
        layers[kind] = PackLayerData(
            kind=kind,
            domain=layer_domain.get(kind, kind),
            root_fn=layer.root_fn,
            root=layer.root,
            rows=tuple(rows),
        )

    # -- FIX-2: make L0.5 (referential closure) + L0.6 (omission honesty) LIVE -- #
    # Previously this reconstructed BOTH the universe map AND its root from the
    # PRESENT rows, so declared≡present by construction and the omission keystone
    # never fired on a real loaded pack. Now:
    #   * the universe ROOT is taken from the COMMITTED universe row's
    #     ``selection_key_root`` (the value the exporter sealed over the keys it
    #     INTENDED), while the universe MAP is rebuilt from the present rows — so a
    #     dropped/added/renamed row makes ``map_root(present) != committed`` and
    #     the recompute in ``_check_manifest_roots`` fires (shrunken-universe);
    #   * ``referenced_hashes`` is populated from the ACTUAL cross-references the
    #     selection rows emit (selected_node_version_id, candidate_set_hash) +
    #     the node-version→content-leaf refs, so a removed leaf / dangling ref is
    #     caught (referential-closure break).
    selection_universe: dict[str, str] | None = None
    selection_universe_root: str | None = None
    referenced: dict[str, str] = {}
    source_refs: set[str] = set()
    state = layers.get("state")
    base = layers.get("base")

    def _bodies(layer: PackLayerData | None):
        if layer is None:
            return
        for row in layer.rows:
            body = row.get("object")
            if isinstance(body, dict):
                yield cast("dict[str, Any]", body)

    # Present node_version intrinsic ids + content_leaf ids (the identity space
    # references resolve against) — closure is verified by the checker against
    # both these and the transport object_hashes.
    present_node_version_ids: set[str] = set()
    for body in _bodies(state):
        if body.get("schema") == "lawvm.node_version.v1":
            nv = body.get("node_version_id")
            if isinstance(nv, str):
                present_node_version_ids.add(nv)
    for body in _bodies(base):
        if body.get("schema") == SCHEMA_CONTENT_LEAF:
            for loc in body.get("source_locators", []) or []:
                if isinstance(loc, str):
                    source_refs.add(loc)

    if state is not None:
        present_map: dict[str, str] = {}
        committed_key_root: str | None = None
        for row in state.rows:
            body = row.get("object")
            if not isinstance(body, dict):
                continue
            typed = cast("dict[str, Any]", body)
            schema = typed.get("schema")
            if schema == "lawvm.selection_row.v1":
                key = typed.get("selection_key")
                nv = typed.get("selected_node_version_id")
                cs = typed.get("candidate_set_hash")
                if isinstance(key, str):
                    # The universe MapRoot the exporter sealed maps
                    # selection_key -> selection_row OBJECT hash.
                    present_map[key] = str(row["object_hash"])
                    if isinstance(nv, str):
                        referenced[f"selected_node_version:{key}"] = nv
                    if isinstance(cs, str):
                        referenced[f"candidate_set:{key}"] = cs
            elif schema == "lawvm.selection_universe.v1":
                root = typed.get("selection_key_root")
                if isinstance(root, str):
                    committed_key_root = root
        if present_map:
            selection_universe = present_map
            # Authoritative root = the COMMITTED universe row's sealed MapRoot;
            # fall back to recompute only if the pack predates the universe row.
            selection_universe_root = committed_key_root or map_root(
                "selection_universe", present_map
            )

    # FIX-3 (partial) — read the cert/ singleton so the checker can re-root it.
    certificate_body: dict[str, Any] | None = None
    cert_file = pack_path / "cert" / "certificate.json"
    if cert_file.exists():
        cert_row = json.loads(cert_file.read_text(encoding="utf-8"))
        cert_obj = cert_row.get("object") if isinstance(cert_row, dict) else None
        if isinstance(cert_obj, dict):
            certificate_body = cast("dict[str, Any]", cert_obj)

    return Pack(
        manifest=manifest,
        layers=layers,
        selection_universe=selection_universe,
        selection_universe_root=selection_universe_root,
        referenced_hashes=referenced,
        source_refs=tuple(sorted(source_refs)),
        recompute_manifest_roots=True,
        certificate_body=certificate_body,
        known_schemas=_KNOWN_SCHEMAS,
    )


_KNOWN_SCHEMAS = frozenset(
    {
        SCHEMA_WORK,
        SCHEMA_ADDRESS_NODE,
        SCHEMA_CONTENT_LEAF,
        SCHEMA_NODE_VERSION,
        SCHEMA_CHECKPOINT,
        SCHEMA_TRANSITION,
        SCHEMA_FINDING,
        SCHEMA_RESIDUAL,
        SCHEMA_COVERAGE,
        SCHEMA_CERTIFICATE,
        SCHEMA_SOURCE_ARTIFACT,
        # The relation-edge schema is a KNOWN schema: the checker's L0.8
        # ``_check_relation_edge_authority`` validates every such row (identity +
        # §25.3 authority-legality matrix), so the ``edges`` layer is genuinely
        # SUPPORTED, not tagged as an unknown overlay.
        SCHEMA_RELATION_EDGE,
        "lawvm.selection_row.v1",
        "lawvm.applicability_fact.v1",
        "lawvm.selection_candidate_set.v1",
        "lawvm.scope_predicate.v1",
        "lawvm.selection_profile.v1",
        "lawvm.selection_universe.v1",
        "lawvm.initial_state_event.v1",
    }
)


def _manifest_from_body(body: dict[str, Any]) -> PackManifest:
    """Reconstruct a PackManifest from its emitted canonical body."""
    layers = tuple(
        PackLayer(
            kind=layer["kind"],
            path=layer["path"],
            row_schema=layer["row_schema"],
            codec=layer["codec"],
            dict_id=layer["dict_id"],
            uncompressed_sha256=layer["uncompressed_sha256"],
            storage_sha256=layer["storage_sha256"],
            root=layer["root"],
            root_fn=layer["root_fn"],
            row_count=layer["row_count"],
        )
        for layer in body["layers"]
    )
    prov = body["provenance"]
    provenance = PackProvenance(
        lawvm_git_commit=prov["lawvm_git_commit"],
        engine_version=prov["engine_version"],
        source_policy_id=prov["source_policy_id"],
        checkable_source_bundle_policy=prov["checkable_source_bundle_policy"],
        created_at=prov["created_at"],
        dirty_tree=prov["dirty_tree"],
    )
    return PackManifest(
        pack_kind=body["pack_kind"],
        work_ids=tuple(body["work_ids"]),
        corpus_version=body["corpus_version"],
        identity_encoding=body["identity_encoding"],
        storage_codec=body["storage_codec"],
        dict_id=body["dict_id"],
        profiles=tuple(body["canonicalization_profiles"]),
        selection_profiles=tuple(body["selection_profiles"]),
        schemas=dict(body["schemas"]),
        layers=layers,
        roots=dict(body["roots"]),
        required_layers_for_browse=tuple(body["required_layers_for_browse"]),
        required_layers_for_audit=tuple(body["required_layers_for_audit"]),
        optional_layers=tuple(body["optional_layers"]),
        provenance=provenance,
        supersedes_pack_id=body.get("supersedes_pack_id"),
        # v0 forward-compat reservations (design §24.1) — omit-when-absent, so a
        # manifest emitted without them round-trips to the byte-identical pack_id.
        corpus_totality_root=body.get("corpus_totality_root"),
        signature_attestation_root=body.get("signature_attestation_root"),
        signatures=tuple(body.get("signatures", ())),
    )
