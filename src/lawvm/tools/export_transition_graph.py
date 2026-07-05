"""Export a certified transition graph for a replayed statute (Design D).

LawVM's Python replay engine is the only authority for resolving legal targets
and interpreting amendment language. This exporter runs that engine once for a
statute, then re-materializes the point-in-time tree at every change-date and
emits a self-contained SQLite database that a browser can use to RENDER the
statute and optionally FOLD CERTIFIED PATCHES without ever resolving legal
targets or interpreting amendment text itself.

Three operation levels (see the module docstring chain in the design notes):

* L1 source ops (amendment language) live only inside the compiler.
* L2 resolved legal operations ("at address A, effective D, replace/insert/
  repeal payload P") are produced by the engine and carried for *display* on
  each transition (``legal_op_kind`` / ``legal_op_summary``).
* L3 certified tree transitions ("at path P with subtree hash H_pre, set/delete
  to payload Q; resulting subtree hash H_post") are the cheap, safe artifacts a
  JS reducer can apply with hash assertions.

The schema is documented in ``SCHEMA_VERSION`` and the ``CREATE TABLE``
statements below. All cross-referenceable entities use canonical GLOBAL ids
(statute ids, source ids, preparatory/source-reference ids, address strings);
content subtrees are de-duplicated by sha256.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir_helpers import structural_subtree_hash as structural_subtree_hash
from lawvm.tools.transition_graph_interlinks import (
    InterlinkTargetPreviewContext,
    LawvmInterlinkExportProvider,
    LawvmInterlinkRow as LawvmInterlinkRow,
    RenderedTextSegment,
    enrich_lawvm_interlink_targets,
    place_lawvm_interlinks,
    placement_summary,
    rendered_text_segments,
)
from lawvm.tools.transition_graph_overlays import (
    SURFACE_OVERLAY_ROW_COLUMNS,
    LawvmSurfaceOverlayExportProvider,
    overlay_row_sql_values,
    place_lawvm_surface_overlays,
)
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile

SCHEMA_VERSION = "transition-graph.v1"

# Base-version sentinel effective date used by compile_timelines for the
# original (unamended) provision content. It is not a real calendar change-date.
_BASE_SENTINEL_DATE = "0000-00-00"

@dataclasses.dataclass(frozen=True, slots=True)
class TransitionRow:
    transition_id: str
    sequence: int
    effective_date: str
    expires_date: str
    action: str
    target_address: str
    pre_hash: str
    post_hash: str
    payload_hash: str
    legal_op_kind: str
    legal_op_summary: str
    source_id: str
    he_ref: str
    source_ref: str
    flags: str

    def with_source_ref(self, source_ref: str) -> "TransitionRow":
        return dataclasses.replace(self, he_ref=source_ref, source_ref=source_ref)

    def sql_values(self) -> tuple[object, ...]:
        return (
            self.transition_id,
            self.sequence,
            self.effective_date,
            self.expires_date,
            self.action,
            self.target_address,
            self.pre_hash,
            self.post_hash,
            self.payload_hash,
            self.legal_op_kind,
            self.legal_op_summary,
            self.source_id,
            self.he_ref,
            self.source_ref,
            self.flags,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class SourceArtifactRow:
    source_id: str
    kind: str
    canonical_id: str
    title: str
    url: str
    content_hash: str
    date: str

    def sql_values(self) -> tuple[object, ...]:
        return (
            self.source_id,
            self.kind,
            self.canonical_id,
            self.title,
            self.url,
            self.content_hash,
            self.date,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class CheckpointRow:
    date: str
    address_prefix: str
    tree_hash: str
    active_node_count: int

    def sql_values(self) -> tuple[object, ...]:
        return (self.date, self.address_prefix, self.tree_hash, self.active_node_count)


@dataclasses.dataclass(frozen=True, slots=True)
class ActiveAtRow:
    date: str
    address: str
    content_hash: str
    transition_id: str = ""

    def with_transition_id(self, transition_id: str) -> "ActiveAtRow":
        return dataclasses.replace(self, transition_id=transition_id)

    def sql_values(self) -> tuple[object, ...]:
        return (self.date, self.address, self.content_hash, self.transition_id)


@dataclasses.dataclass(frozen=True, slots=True)
class DisplayNodeRow:
    date: str
    address: str
    kind: str
    label: str
    num: str
    heading: str

    def sql_values(self) -> tuple[object, ...]:
        return (self.date, self.address, self.kind, self.label, self.num, self.heading)


@dataclasses.dataclass(frozen=True, slots=True)
class EdgeRow:
    edge_id: str
    kind: str
    from_id: str
    to_id: str
    payload: str

    def sql_values(self) -> tuple[object, ...]:
        return (self.edge_id, self.kind, self.from_id, self.to_id, self.payload)


@dataclasses.dataclass(frozen=True, slots=True)
class DerivationEdgeRow:
    """One typed FI derivation/relation edge projected into the exported graph.

    Carries the substrate ``lawvm.legal_relation_edge.v0`` identity (content-
    addressed ``edge_id``, ``relation_kind``, ``authority_plane``) plus the
    FI-layer ``derivation_kind`` (textual | model_code | conformance | citation)
    so a viewer can render the four categorically-distinct relationships WITHOUT
    re-deriving them, and can never read a textual byte-match as a lineage claim
    (the non-conflation is carried in the row). The full edge body is stored as
    ``edge_json`` so the checkable claim (edit-script id, text hashes, honesty
    boundary) travels intact.
    """

    edge_id: str
    derivation_kind: str
    relation_kind: str
    authority_plane: str
    source_ref: str
    target_ref: str
    replay_authorized: int
    edge_status: str
    edge_json: str

    def sql_values(self) -> tuple[object, ...]:
        return (
            self.edge_id,
            self.derivation_kind,
            self.relation_kind,
            self.authority_plane,
            self.source_ref,
            self.target_ref,
            self.replay_authorized,
            self.edge_status,
            self.edge_json,
        )


@dataclasses.dataclass(frozen=True, slots=True)
class EvidenceEventRow:
    event_id: str
    surface: str
    kind: str
    role: str
    severity: str
    phase: str
    source_id: str
    effective_date: str
    target_address: str
    rule_id: str
    title: str
    detail_json: str

    def sql_values(self) -> tuple[object, ...]:
        return (
            self.event_id,
            self.surface,
            self.kind,
            self.role,
            self.severity,
            self.phase,
            self.source_id,
            self.effective_date,
            self.target_address,
            self.rule_id,
            self.title,
            self.detail_json,
        )


# ---------------------------------------------------------------------------
# Structural subtree hashing (L3 certification primitive)
# ---------------------------------------------------------------------------


# Canonical implementation lives in lawvm.core.ir_helpers so the apply-time
# WriteReceipt producer and this exporter share the single frozen recipe
# (CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2). Re-exported here because this
# module historically owned it.


def _subtree_json(node: IRNode) -> bytes:
    """Canonical JSON encoding of an IRNode subtree for content_blobs storage."""
    return json.dumps(node.to_jsonable_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# Address traversal
# ---------------------------------------------------------------------------


def _node_address_string(path: Tuple[Tuple[str, str], ...]) -> str:
    """Render a node path as a canonical address string ("chapter:1/section:3")."""
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _unique_child_label(child: IRNode, kind: str, label: str, used: Dict[Tuple[str, str], int]) -> str:
    """Disambiguate a child's address label against earlier same-kind/label siblings.

    Covering/display addresses are keyed by ``kind:label``; that is unique under a
    well-formed tree but NOT under source anomalies — some enacted UK XML carries
    two siblings with the same printed number (e.g. RTA 1988 s.113 has two
    ``<Pnumber>2</Pnumber>`` subsections, ids ``section-113-2n1``/``-2n2``, and no
    (1)). Those are faithful source data that must round-trip, not be deduped. The
    first occurrence keeps the bare label (so ordinary trees — all of Finland — are
    unchanged); a colliding sibling is suffixed with its stable source id (eId/id)
    or, failing that, its occurrence ordinal, so each node gets a distinct address
    and the covering set tiles without overlap.
    """
    key = (kind, label)
    seen = used.get(key, 0)
    used[key] = seen + 1
    if seen == 0:
        return label
    source_key = str(child.attrs.get("eId") or child.attrs.get("id") or "").strip()
    return f"{label}#{source_key or seen + 1}"


def _iter_addressed_nodes(
    root: IRNode,
    prefix: Tuple[Tuple[str, str], ...] = (),
) -> List[Tuple[str, IRNode]]:
    """Yield (address_string, node) for every labeled descendant of ``root``.

    The root body itself is unlabeled and skipped; labeled structural nodes
    (chapters, sections, subsections, ...) become addressable rows. Returned in
    document order. Colliding same-kind/label siblings are disambiguated (see
    :func:`_unique_child_label`) so addresses stay unique even over anomalous
    source.
    """
    out: List[Tuple[str, IRNode]] = []
    used: Dict[Tuple[str, str], int] = {}
    for child in root.children:
        kind = str(child.kind)
        label = child.label or ""
        if label:
            path = prefix + ((kind, _unique_child_label(child, kind, label, used)),)
            out.append((_node_address_string(path), child))
            out.extend(_iter_addressed_nodes(child, path))
        else:
            # Unlabeled wrapper (e.g. heading prose): descend without extending.
            out.extend(_iter_addressed_nodes(child, prefix))
    return out


# Covering-frontier granularity. A covering unit is the deepest labeled node on
# each root-to-leaf path whose kind is in ``stop_kinds`` (the target depth) OR
# which has no labeled descendant of a ``stop_kind`` (a shallower leaf-stable
# unit). Structural ancestors (chapters, sections above a labeled subsection)
# are traversed through, never emitted, so the frontier still tiles the whole
# tree with no overlap — only the granularity of the tiling changes.
#
# "section" (legacy) tiled at chapter/top-level-section depth; "subsection"
# (default) descends to the §a:b.c subsection units the per-§ version trail
# needs, falling back to the section itself when a section has no labeled
# subsection children. The set of stop kinds is inclusive of everything down to
# the granularity so that, e.g., a section that is itself the leaf becomes a
# covering unit rather than being dropped.
_GRANULARITY_STOP_KINDS: Dict[str, frozenset[str]] = {
    # Legacy whole-chapter tiling: stop at the shallowest labeled node.
    "chapter": frozenset(),
    # Section tiling: descend chapters, stop at sections.
    "section": frozenset({"section"}),
    # Subsection tiling (default): descend to labeled subsections; sections with
    # no labeled subsection child stay whole.
    "subsection": frozenset({"subsection"}),
}

DEFAULT_GRANULARITY = "subsection"


def covering_units(
    root: IRNode,
    slice_prefix: str = "",
    granularity: str = DEFAULT_GRANULARITY,
) -> List[Tuple[str, IRNode]]:
    """Return the document-ordered covering set of addressable units.

    A covering unit is the deepest labeled node on each root-to-leaf path that is
    either at the requested ``granularity`` (``stop_kinds``) or is leaf-stable
    (has no labeled descendant of a stop kind). The covering units' full subtrees
    collectively reconstruct the whole (sliced) tree with no overlap, so a JS
    reducer can fold ``set_subtree`` / ``delete_subtree`` over them and rebuild +
    hash the entire tree.

    ``granularity``:
      * ``"chapter"``  — legacy: shallowest labeled node (chapters / top-level
        sections / heading). Coarse; one unit per chapter.
      * ``"section"``  — descend chapters, emit sections.
      * ``"subsection"`` (default) — descend to labeled subsections; a section
        with no labeled subsection child is itself the unit. This is what gives
        the certified graph section/subsection-granular transitions.

    When ``slice_prefix`` is set, only units at or below that prefix are emitted
    (ancestors of the slice are traversed through to reach it).
    """
    stop_kinds = _GRANULARITY_STOP_KINDS.get(granularity)
    if stop_kinds is None:
        raise ValueError(f"unknown granularity {granularity!r}; expected one of {sorted(_GRANULARITY_STOP_KINDS)}")
    out: List[Tuple[str, IRNode]] = []

    def _has_stop_descendant(node: IRNode) -> bool:
        """True if ``node`` has any labeled descendant whose kind is a stop kind."""
        for child in node.children:
            if (child.label or "") and str(child.kind) in stop_kinds:
                return True
            if _has_stop_descendant(child):
                return True
        return False

    def _emit_or_descend(node: IRNode, path: Tuple[Tuple[str, str], ...], addr: str) -> None:
        """Emit ``node`` as a covering unit, or descend if it is a structural
        ancestor of finer stop-kind units."""
        kind = str(node.kind)
        # Stop here when this node is itself at the target granularity, or when
        # nothing deeper reaches the target granularity (leaf-stable unit).
        if kind in stop_kinds or not _has_stop_descendant(node):
            out.append((addr, node))
            return
        _walk(node, path)

    def _walk(node: IRNode, prefix: Tuple[Tuple[str, str], ...]) -> None:
        used: Dict[Tuple[str, str], int] = {}
        for child in node.children:
            kind = str(child.kind)
            label = child.label or ""
            if label:
                path = prefix + ((kind, _unique_child_label(child, kind, label, used)),)
                addr = _node_address_string(path)
                if not slice_prefix:
                    _emit_or_descend(child, path, addr)
                    continue
                if addr == slice_prefix or addr.startswith(slice_prefix + "/"):
                    _emit_or_descend(child, path, addr)
                elif slice_prefix.startswith(addr + "/") or slice_prefix == addr:
                    # ancestor of the slice: descend to reach the slice
                    _walk(child, path)
                # else: outside slice, skip
            else:
                _walk(child, prefix)

    _walk(root, ())
    return out


def _child_text(node: IRNode, kind: str) -> str:
    for child in node.children:
        if str(child.kind) == kind and child.text:
            return child.text.strip()
    return ""


def display_node_rows(
    date: str,
    root: IRNode,
    slice_prefix: str = "",
    active_addresses: frozenset[str] = frozenset(),
) -> List[DisplayNodeRow]:
    """Display metadata for every addressed node visible in a rendered slice.

    Fine-grained exports can store only subsection blobs in ``active_at``; the
    viewer then synthesizes chapter/section scaffold rows from addresses. Those
    scaffold rows still need engine-authored headings. Rows already present in
    ``active_at`` carry their own blob metadata and are intentionally skipped.
    """
    rows: List[DisplayNodeRow] = []
    for addr, node in _iter_addressed_nodes(root):
        if addr in active_addresses:
            continue
        if slice_prefix and not (
            addr == slice_prefix
            or addr.startswith(slice_prefix + "/")
            or slice_prefix.startswith(addr + "/")
        ):
            continue
        rows.append(
            DisplayNodeRow(
                date=date,
                address=addr,
                kind=str(node.kind),
                label=str(node.label or ""),
                num=_child_text(node, "num"),
                heading=_child_text(node, "heading"),
            )
        )
    return rows


def reproducible_tree_hash(units: List[Tuple[str, str]]) -> str:
    """Hash an (address, subtree_hash) covering set, ordered by address.

    This is the certified checkpoint hash. It is reproducible by a JS reducer
    that folds the same covering-unit transitions, because it depends only on
    the covering set and each unit's subtree hash (sorted by address for a
    canonical order) — never on engine internals or document order. Document
    order for rendering is preserved separately via ``active_at`` rowid order.
    Renumbers/relabels change the address itself, so structural reordering that
    matters legally is still reflected in the hash.
    """
    h = hashlib.sha256()
    for addr, subtree_hash in sorted(units, key=lambda u: u[0]):
        h.update(addr.encode("utf-8"))
        h.update(b"\x00")
        h.update(subtree_hash.encode("utf-8"))
        h.update(b"\x01")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Change-date computation
# ---------------------------------------------------------------------------


def resolve_commencement_date(
    timelines: Dict[LegalAddress, Any],
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> str:
    """Resolve the statute's real commencement (display axis), or ``""``.

    The engine seeds the original (unamended) provision content at the
    ``0000-00-00`` base sentinel, which is load-bearing for ``--query-type
    governing`` but is NOT a real calendar date. For the viewer/cert *display*
    axis we need the real commencement so the as-enacted version anchors at the
    date the law actually came into force instead of at its first amendment.

    Source precedence:

    1. The jurisdiction profile's commencement resolver. When the jurisdiction
       has no unique legal commencement witness, it returns ``""``.
    2. The FRBR issue/signature date (``enacted``) carried on the base-version
       provisions (effective == ``0000-00-00``).
    3. Otherwise ``""`` (caller keeps the sentinel-dropping behavior).
    """
    export_profile = profile or _default_export_profile()
    commencement = export_profile.commencement_date(timelines)
    if commencement:
        return commencement

    # FRBR fallback: the enacted date stamped on base-version provisions.
    enacted: set[str] = set()
    for timeline in timelines.values():
        for version in timeline.versions:
            if version.effective == _BASE_SENTINEL_DATE and version.enacted:
                enacted.add(version.enacted)
    if len(enacted) == 1:
        return next(iter(enacted))
    return ""


def compute_change_dates(
    timelines: Dict[LegalAddress, Any],
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> List[str]:
    """Return the sorted list of real calendar change-dates from timelines.

    The union of every version effective/expires date. The ``0000-00-00`` base
    sentinel is the engine's placeholder for the original (unamended) content;
    for the display axis it is SUBSTITUTED with the statute's real commencement
    (see :func:`resolve_commencement_date`) rather than dropped, so that
    ``change_dates[0]`` is the commencement — the as-enacted version's window
    then reads ``<commencement>–<first amendment>`` instead of starting at the
    first amendment. Empty strings are excluded. An expiry date D is itself a
    change-date: the provision is gone on/after D, so the tree at D differs.

    Only the EXPORT/display axis is affected; the engine seed and
    ``compile_timelines``' base ``effective`` keep the ``0000-00-00`` sentinel
    (load-bearing for ``--query-type governing``).
    """
    dates: set[str] = set()
    commencement = resolve_commencement_date(timelines, profile=profile)
    if commencement:
        dates.add(commencement)
    for timeline in timelines.values():
        for version in timeline.versions:
            if version.effective and version.effective != _BASE_SENTINEL_DATE:
                dates.add(version.effective)
            if version.expires:
                dates.add(version.expires)
    return sorted(dates)


# ---------------------------------------------------------------------------
# L2 op indexing (for display annotation on transitions)
# ---------------------------------------------------------------------------


def _legal_op_summary(op: Any) -> str:
    """One-line human summary of a resolved L2 LegalOperation."""
    parts = [str(op.action)]
    if op.target is not None:
        parts.append(str(op.target))
    if op.destination is not None:
        parts.append(f"-> {op.destination}")
    src = op.source
    if src is not None and src.statute_id:
        parts.append(f"[{src.statute_id}]")
    return " ".join(parts)


def _index_ops_by_date(lo_ops: List[Any]) -> Dict[str, List[Any]]:
    """Map effective_date -> [ops] for L2 display annotation.

    Used only to attach L2 display metadata to L3 transitions; never to resolve
    anything. Effective date comes from the op's source provenance.
    """
    index: Dict[str, List[Any]] = {}
    for op in lo_ops:
        src = op.source
        eff = (src.effective if src is not None else "") or ""
        index.setdefault(eff, []).append(op)
    return index


def _index_ops_by_expiry_date(lo_ops: List[Any]) -> Dict[str, List[Any]]:
    """Map expires-date -> [ops] whose fixed-term validity ends that day.

    A temporary act's scheduled lapse produces a real L3 transition on the
    expiry date with no op *effective* that day; this index lets the exporter
    attribute that transition to the act that scheduled the expiry instead of
    exporting an unexplained deletion/reversion.
    """
    index: Dict[str, List[Any]] = {}
    for op in lo_ops:
        src = op.source
        exp = (src.expires if src is not None else "") or ""
        if exp:
            index.setdefault(exp, []).append(op)
    return index


def _address_top_kind(address: str) -> str:
    """Return the kind of an address's first segment (e.g. 'section' from 'section:40')."""
    if not address:
        return ""
    return address.split("/", 1)[0].split(":", 1)[0]


def _align_address_to_kind(covering_address: str, target_kind: str) -> str:
    """Drop leading container segments from ``covering_address`` above ``target_kind``.

    UK op targets are addressed from the section/schedule level (``section:40``,
    ``schedule:24/paragraph:1``) while covering-unit addresses are the full tree
    path and carry leading container segments (``part:Part I/section:40``). To
    attribute an op to a covering unit we realign the covering address at the
    op-target's top kind. Returns "" when that kind is absent. FI op targets are
    already container-qualified to the same depth as their covering addresses, so
    this realignment is a no-op there (the first segment is already the target's
    kind).
    """
    if not target_kind:
        return ""
    segments = covering_address.split("/")
    for i, seg in enumerate(segments):
        if seg.split(":", 1)[0] == target_kind:
            return "/".join(segments[i:])
    return ""


def _ops_for_covering(ops_on_date: List[Any], covering_address: str) -> List[Any]:
    """Return ops on a date that provenance-attribute to ``covering_address``.

    An op attributes to a changed covering unit when its resolved target is:

    * exactly the covering address,
    * a descendant of it (``target`` startswith ``covering_address + "/"``) —
      e.g. a §a.2 amendment landing inside a section covering unit, or
    * an ancestor of it (``covering_address`` startswith ``target + "/"``) —
      e.g. a whole-section replace whose derived change is observed at the
      subsection units that tile that section.

    The ancestor case is what carries amendment provenance down to the finer
    subsection/paragraph transitions: when only the whole section is the op
    target but the diff materialized at subsection granularity, every changed
    subsection of that section is attributed to the amending säädös.

    The same three relations are retried after realigning the covering address
    at the op-target's top kind (see :func:`_align_address_to_kind`), so ops
    addressed from a shallower level than the covering path — UK section/schedule
    targets under part-qualified covering addresses — still attribute. FI targets
    align at the first segment already, so the retry is a no-op there.
    """
    out: List[Any] = []
    for op in ops_on_date:
        target = str(op.target) if op.target is not None else ""
        if not target:
            continue
        if (
            target == covering_address
            or target.startswith(covering_address + "/")
            or covering_address.startswith(target + "/")
        ):
            out.append(op)
            continue
        aligned = _align_address_to_kind(covering_address, _address_top_kind(target))
        if aligned and (
            target == aligned
            or target.startswith(aligned + "/")
            or aligned.startswith(target + "/")
        ):
            out.append(op)
    return out


# ---------------------------------------------------------------------------
# Engine invocation + per-date oracle materialization
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayBundle:
    """Everything captured from a single authoritative engine replay."""

    statute_id: str  # canonical "301/2004"
    engine_id: str  # engine-facing "2004/301" (drives op-target/timeline keying)
    title: str
    result: Any  # ReplayResult
    lo_ops: List[Any]
    timelines: Dict[LegalAddress, Any]
    change_dates: List[str]
    replay_findings: List[Any] = dataclasses.field(default_factory=list)
    failed_ops: List[Any] = dataclasses.field(default_factory=list)
    source_pathologies: List[Any] = dataclasses.field(default_factory=list)
    materialization_cache: Dict[object, object] = dataclasses.field(default_factory=dict)


def run_engine_replay(
    statute_id_yearnum: str,
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> ReplayBundle:
    """Compatibility wrapper for direct callers using the Finnish adapter.

    New jurisdiction-aware callers should pass the adapter's ``replay_runner``
    into :func:`export_transition_graph`; this shim exists so older synthetic
    tests can still monkeypatch one name.
    """
    from lawvm.finland.transition_graph_replay import run_fi_transition_graph_replay

    export_profile = profile or _default_export_profile()
    return run_fi_transition_graph_replay(statute_id_yearnum, profile=export_profile)


def _op_variant_kind(op: Any) -> str:
    """Return 'temporary' when the op carries a source-side expiry, else 'permanent'."""
    src = op.source
    if src is not None and (src.expires or ""):
        return "temporary"
    return "permanent"


def _sidecar_base_body(result: Any) -> IRNode:
    """Return the unamended-statute root IRNode from an adapter replay state.

    Finnish replay states expose ``result.ctx.base_ir`` (already an IRNode body).
    Generic IRStatute-shaped states (e.g. UK) expose ``result.base_ir`` with a
    ``body`` + ``supplements`` split; both are wrapped under one addressable root
    so the browser-side L2 folder sees the same covering surface the certified
    transitions tile.
    """
    ctx = getattr(result, "ctx", None)
    if ctx is not None and getattr(ctx, "base_ir", None) is not None:
        return ctx.base_ir
    base_ir = getattr(result, "base_ir", None)
    if base_ir is not None and getattr(base_ir, "body", None) is not None:
        children = (*base_ir.body.children, *getattr(base_ir, "supplements", ()))
        return IRNode(kind=base_ir.body.kind, label=None, text="", children=tuple(children))
    raise AttributeError(
        "replay state exposes neither ctx.base_ir nor an IRStatute-shaped base_ir "
        "for the L2 sidecar base body"
    )


def _sidecar_migration_events(result: Any) -> List[Any]:
    """Return the replay state's migration events, or [] when none are tracked."""
    products = getattr(result, "products", None)
    if products is None:
        return []
    return list(getattr(products, "migration_events", ()) or [])


def emit_l2_sidecar(
    bundle: ReplayBundle,
    checkpoints: List[CheckpointRow],
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> Dict[str, Any]:
    """Build the JSON sidecar for independent browser-side L2 replay (Exp-2).

    Carries the base body tree and the full resolved L2 operation stream with
    the temporal/structural fields a JS folder needs: effective/expires dates,
    same-day ``sequence``, action, target/destination/anchor addresses, payload
    subtree, and variant kind. Plus the engine oracle checkpoint hashes so the
    JS folder can self-score WITHOUT consulting the certified transition graph.
    """
    result = bundle.result
    export_profile = profile or _default_export_profile()
    base_body = _sidecar_base_body(result)  # IRNode root of the unamended statute
    ops_json: List[Dict[str, Any]] = []
    for op in bundle.lo_ops:
        src = op.source
        text_patch = None
        if op.text_patch is not None:
            tp = op.text_patch
            text_patch = {
                "kind": str(tp.kind),
                "match_text": tp.selector.match_text,
                "occurrence": tp.selector.occurrence,
                "end_occurrence": tp.selector.end_occurrence,
                "replacement": tp.replacement,
            }
        ops_json.append(
            {
                "op_id": op.op_id,
                "sequence": op.sequence,
                "action": str(op.action),
                "target": str(op.target) if op.target is not None else "",
                "anchor": str(op.anchor) if op.anchor is not None else "",
                "destination": str(op.destination) if op.destination is not None else "",
                "effective": (src.effective if src is not None else "") or "",
                "expires": (src.expires if src is not None else "") or "",
                "enacted": (src.enacted if src is not None else "") or "",
                "source_statute": export_profile.canonical_statute_id(src.statute_id) if src is not None and src.statute_id else "",
                "variant_kind": _op_variant_kind(op),
                "group_id": op.group_id or "",
                "payload": op.payload.to_jsonable_dict() if op.payload is not None else None,
                "text_patch": text_patch,
            }
        )
    migrations_json = [
        {
            "kind": me.kind,
            "from_address": str(me.from_address),
            "to_address": str(me.to_address),
            "effective": me.effective or "",
            "source_statute": export_profile.canonical_statute_id(me.source_statute) if me.source_statute else "",
        }
        for me in _sidecar_migration_events(result)
    ]
    return {
        "statute_id": bundle.statute_id,
        "title": bundle.title,
        "schema_version": SCHEMA_VERSION,
        "change_dates": bundle.change_dates,
        "base_body": base_body.to_jsonable_dict(),
        "ops": ops_json,
        "migration_events": migrations_json,
        "oracle_checkpoints": [
            {"date": row.date, "tree_hash": row.tree_hash, "active_node_count": row.active_node_count}
            for row in checkpoints
        ],
    }


def materialize_oracle_tree(bundle: ReplayBundle, as_of: str) -> IRNode:
    """Compatibility wrapper for direct callers using the Finnish adapter.

    New jurisdiction-aware callers should pass the adapter's materializer into
    :func:`export_transition_graph`; this shim exists so older synthetic tests
    can still monkeypatch one name.
    """
    from lawvm.finland.transition_graph_replay import materialize_fi_transition_graph_tree

    return materialize_fi_transition_graph_tree(bundle, as_of)


# ---------------------------------------------------------------------------
# Export profile and compatibility id helpers
# ---------------------------------------------------------------------------


def _default_export_profile() -> TransitionGraphExportProfile:
    """Return the compatibility profile for legacy direct Python callers.

    CLI callers must select through ``transition_graph_adapter_for_jurisdiction``;
    this fallback preserves old tests and direct calls while keeping
    viewer-facing metadata, URL, source-ref, and corpus conventions outside the
    neutral interlink/cache helpers.
    """
    from lawvm.finland.transition_graph_profile import finland_transition_graph_export_profile

    return finland_transition_graph_export_profile()


def _canonical_statute_id(statute_id: str) -> str:
    """Compatibility wrapper for existing Finland transition-graph callers."""
    from lawvm.finland.statute_id import canonical_statute_id

    return canonical_statute_id(statute_id)


def _engine_statute_id(statute_id: str) -> str:
    """Compatibility wrapper for existing Finland transition-graph callers."""
    from lawvm.finland.statute_id import engine_statute_id

    return engine_statute_id(statute_id)


def _json_safe(value: Any) -> Any:
    """Return a JSON-serializable projection without dropping evidence fields."""
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _detail_json(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True)


def _canonical_source_id(
    value: object,
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> str:
    source_id = str(value or "").strip()
    if not source_id:
        return ""
    export_profile = profile or _default_export_profile()
    return export_profile.canonical_statute_id(source_id)


def _source_effective_dates(
    lo_ops: List[Any],
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> Dict[str, str]:
    """Return source_id -> effective date only when the mapping is unambiguous."""
    export_profile = profile or _default_export_profile()
    by_source: Dict[str, set[str]] = {}
    for op in lo_ops:
        src = getattr(op, "source", None)
        source_id = _canonical_source_id(
            getattr(src, "statute_id", "") if src is not None else "",
            profile=export_profile,
        )
        effective_raw = getattr(src, "effective", "") if src is not None else ""
        effective = str(effective_raw or "")
        if source_id and effective:
            by_source.setdefault(source_id, set()).add(effective)
    return {source_id: next(iter(dates)) for source_id, dates in by_source.items() if len(dates) == 1}


def _address_from_scope(
    *,
    target_unit_kind: object = "",
    target_label: object = "",
    target_section: object = "",
    target_chapter: object = "",
    target_part: object = "",
) -> str:
    kind = str(target_unit_kind or "")
    label = str(target_label or target_section or "")
    chapter = str(target_chapter or "")
    part = str(target_part or "")
    if kind == "part" and (label or part):
        return f"part:{label or part}"
    if kind == "chapter" and (label or chapter):
        return f"chapter:{label or chapter}"
    if kind == "section" and label:
        return f"chapter:{chapter}/section:{label}" if chapter else f"section:{label}"
    return ""


def _target_address_from_detail(detail: Dict[str, Any]) -> str:
    explicit = str(detail.get("target_address") or detail.get("address") or "")
    if explicit:
        return explicit
    return _address_from_scope(
        target_unit_kind=detail.get("target_unit_kind", ""),
        target_label=detail.get("target_label", ""),
        target_section=detail.get("target_section", ""),
        target_chapter=detail.get("target_chapter", ""),
        target_part=detail.get("target_part", ""),
    )


def build_evidence_event_rows(
    bundle: ReplayBundle,
    *,
    profile: TransitionGraphExportProfile | None = None,
) -> List[EvidenceEventRow]:
    """Project internal LawVM uncertainty/evidence into viewer-safe rows."""
    export_profile = profile or _default_export_profile()
    source_dates = _source_effective_dates(bundle.lo_ops, profile=export_profile)
    rows: List[EvidenceEventRow] = []

    def add(
        *,
        surface: str,
        kind: str,
        role: str,
        severity: str,
        phase: str,
        source_id: str,
        target_address: str,
        rule_id: str,
        title: str,
        detail: Any,
    ) -> None:
        event_id = f"ev{len(rows) + 1:06d}"
        rows.append(
            EvidenceEventRow(
                event_id=event_id,
                surface=surface,
                kind=kind,
                role=role,
                severity=severity,
                phase=phase,
                source_id=source_id,
                effective_date=source_dates.get(source_id, ""),
                target_address=target_address,
                rule_id=rule_id,
                title=title,
                detail_json=_detail_json(detail),
            )
        )

    for finding in bundle.replay_findings:
        detail = dict(getattr(finding, "detail", {}) or {})
        source_id = _canonical_source_id(
            getattr(finding, "source_statute", "") or detail.get("source_statute", ""),
            profile=export_profile,
        )
        role = str(getattr(finding, "role", "") or "")
        blocking = bool(getattr(finding, "blocking", False))
        severity = "error" if blocking or role == "violation" else "warning" if role == "obligation" else "info"
        kind = str(getattr(finding, "kind", "") or "")
        add(
            surface="replay_finding",
            kind=kind,
            role=role,
            severity=severity,
            phase=str(getattr(finding, "stage", "") or ""),
            source_id=source_id,
            target_address=_target_address_from_detail(detail),
            rule_id=kind,
            title=kind,
            detail=finding,
        )

    for pathology in bundle.source_pathologies:
        detail = pathology.as_detail() if hasattr(pathology, "as_detail") else _json_safe(pathology)
        detail_dict = detail if isinstance(detail, dict) else {}
        source_id = _canonical_source_id(
            getattr(pathology, "source_statute", "") or detail_dict.get("source_statute", ""),
            profile=export_profile,
        )
        kind = str(getattr(pathology, "code", "") or detail_dict.get("code", ""))
        add(
            surface="source_pathology",
            kind=kind,
            role="source_pathology",
            severity="warning",
            phase="replay",
            source_id=source_id,
            target_address=_target_address_from_detail(detail_dict),
            rule_id=kind,
            title=str(getattr(pathology, "message", "") or detail_dict.get("message", kind)),
            detail=detail,
        )

    for failed in bundle.failed_ops:
        detail = failed.as_detail() if hasattr(failed, "as_detail") else _json_safe(failed)
        detail_dict = detail if isinstance(detail, dict) else {}
        source_id = _canonical_source_id(
            getattr(failed, "amendment_id", "") or detail_dict.get("amendment_id", ""),
            profile=export_profile,
        )
        reason_code = str(getattr(failed, "reason_code", "") or detail_dict.get("reason_code", ""))
        kind = reason_code or "failed_operation"
        add(
            surface="failed_op",
            kind=kind,
            role="rejected_operation",
            severity="error",
            phase="apply",
            source_id=source_id,
            target_address=_target_address_from_detail(detail_dict),
            rule_id=kind,
            title=str(getattr(failed, "reason", "") or detail_dict.get("reason", kind)),
            detail=detail,
        )

    return rows


# ---------------------------------------------------------------------------
# Derivation / relation-edge projection (FI reference-edge extraction surface)
# ---------------------------------------------------------------------------


def build_derivation_edge_rows(edge_set: Any) -> List[DerivationEdgeRow]:
    """Flatten a FI :class:`DerivationEdgeSet` into export rows, kind-tagged.

    The FI reference-edge classifier (``lawvm.finland.references.derivation_edges``)
    is a READ/PUBLISH projection: it types each relationship into exactly one of
    {textual, model_code, conformance, citation} as a substrate
    ``lawvm.legal_relation_edge.v0`` body and keeps the four kinds in SEPARATE
    lists so the non-conflation is structural. This function carries that same
    typing into the exported graph — it reads the kind back off each edge via
    :meth:`DerivationEdgeSet.kind_of` (the same bytes a consumer reads), never a
    positional guess — so the derivation table cannot silently mislabel a byte
    match as a lineage claim. ``target_set`` is a set by name; each element
    becomes its own row (a multi-target edge fans out) so the table is a flat
    ``(source_ref, target_ref)`` relation keyed by the shared ``edge_id``.
    """
    rows: List[DerivationEdgeRow] = []
    for edge in edge_set.all_edges():
        derivation_kind = edge_set.kind_of(edge).value
        relation_kind = str(edge.get("relation_kind") or "")
        authority_plane = str(edge.get("authority_plane") or "")
        source_ref = str(edge.get("source_ref") or "")
        edge_id = str(edge.get("edge_id") or "")
        edge_status = str(edge.get("edge_status") or "")
        replay_authorized = 1 if bool(edge.get("replay_authorized")) else 0
        edge_json = json.dumps(edge, ensure_ascii=False, sort_keys=True)
        targets = edge.get("target_set") or []
        target_list = list(targets) if isinstance(targets, (list, tuple)) else [targets]
        if not target_list:
            target_list = [""]
        for target in target_list:
            rows.append(
                DerivationEdgeRow(
                    edge_id=edge_id,
                    derivation_kind=derivation_kind,
                    relation_kind=relation_kind,
                    authority_plane=authority_plane,
                    source_ref=source_ref,
                    target_ref=str(target),
                    replay_authorized=replay_authorized,
                    edge_status=edge_status,
                    edge_json=edge_json,
                )
            )
    return rows


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT  -- JSON
);
CREATE TABLE source_artifacts (
    source_id    TEXT PRIMARY KEY,  -- canonical global id (statute/amendment)
    kind         TEXT,              -- 'statute' | 'amendment'
    canonical_id TEXT,
    title        TEXT,
    url          TEXT,
    content_hash TEXT,
    date         TEXT
);
CREATE TABLE content_blobs (
    content_hash TEXT PRIMARY KEY,  -- sha256 of canonical subtree JSON
    content_json BLOB
);
CREATE TABLE transitions (
    transition_id   TEXT PRIMARY KEY,
    sequence        INTEGER,
    effective_date  TEXT,
    expires_date    TEXT,
    action          TEXT,  -- set_subtree|delete_subtree|move_subtree|tombstone|restore
    target_address  TEXT,
    pre_hash        TEXT,
    post_hash       TEXT,
    payload_hash    TEXT,
    legal_op_kind   TEXT,  -- L2 action(s) for display
    legal_op_summary TEXT, -- L2 summary for display
    source_id       TEXT,
    he_ref          TEXT,  -- legacy FI mirror of source_ref
    source_ref      TEXT,  -- jurisdiction-owned source reference token
    flags           TEXT   -- JSON
);
CREATE TABLE edges (
    edge_id  TEXT PRIMARY KEY,
    kind     TEXT,   -- supersedes | created_by | amended_by
    from_id  TEXT,
    to_id    TEXT,
    payload  TEXT    -- JSON
);
CREATE TABLE checkpoints (
    date            TEXT PRIMARY KEY,
    address_prefix  TEXT,
    tree_hash       TEXT,   -- the Python-engine ORACLE tree hash
    active_node_count INTEGER
);
CREATE TABLE active_at (
    date         TEXT,
    address      TEXT,
    content_hash TEXT,
    transition_id TEXT,
    PRIMARY KEY (date, address)
);
CREATE TABLE display_nodes (
    date    TEXT,
    address TEXT,
    kind    TEXT,
    label   TEXT,
    num     TEXT,
    heading TEXT,
    PRIMARY KEY (date, address)
);
CREATE TABLE evidence_events (
    event_id       TEXT PRIMARY KEY,
    surface        TEXT,
    kind           TEXT,
    role           TEXT,
    severity       TEXT,
    phase          TEXT,
    source_id      TEXT,
    effective_date TEXT,
    target_address TEXT,
    rule_id        TEXT,
    title          TEXT,
    detail_json    TEXT
);
CREATE TABLE derivation_edges (
    edge_id           TEXT,   -- content-addressed relation-edge id (shared per fan-out)
    derivation_kind   TEXT,   -- textual | model_code | conformance | citation
    relation_kind     TEXT,   -- substrate lawvm.legal_relation_edge.v0 relation_kind
    authority_plane   TEXT,   -- legal_state | evidence | overlay | surface
    source_ref        TEXT,
    target_ref        TEXT,
    replay_authorized INTEGER,-- 1 only for byte-verified textual derivation
    edge_status       TEXT,
    edge_json         TEXT,   -- full edge body (checkable claim travels intact)
    PRIMARY KEY (edge_id, target_ref)
);
CREATE TABLE lawvm_interlinks (
    interlink_id             TEXT PRIMARY KEY,
    source_jurisdiction      TEXT,
    source_work_kind         TEXT,
    source_local_id          TEXT,
    source_work_id           TEXT,
    source_locator           TEXT,
    surface_text             TEXT,
    surface_kind             TEXT,
    role                     TEXT,
    target_jurisdiction      TEXT,
    target_work_kind         TEXT,
    target_local_id          TEXT,
    target_work_id           TEXT,
    target_locator           TEXT,
    target_url               TEXT,
    candidate_work_ids       TEXT,
    resolution_status        TEXT,
    confidence               TEXT,
    resolver_id              TEXT,
    source_artifact_id       TEXT,
    source_span_byte_offset  INTEGER,
    source_span_byte_len     INTEGER,
    rendered_statute_id      TEXT,
    rendered_effective_date  TEXT,
    rendered_address         TEXT,
    rendered_segment_index   INTEGER,
    rendered_char_start      INTEGER,
    rendered_char_end        INTEGER,
    valid_at_start           TEXT,
    valid_at_end             TEXT,
    detail_json              TEXT
);
CREATE TABLE lawvm_interlink_targets (
    target_key          TEXT PRIMARY KEY,
    target_jurisdiction TEXT,
    target_work_kind    TEXT,
    target_local_id     TEXT,
    target_work_id      TEXT,
    target_locator      TEXT,
    target_url          TEXT,
    target_links_json   TEXT,
    preview_status      TEXT,
    preview_source      TEXT,
    title               TEXT,
    locator_label       TEXT,
    hierarchy_json      TEXT,
    preview_text        TEXT,
    detail_json         TEXT
);
CREATE TABLE lawvm_surface_overlays (
    overlay_id              TEXT PRIMARY KEY,
    statute_id              TEXT,
    kind                    TEXT,  -- closed overlay vocabulary (defined_term, ...)
    node_id                 TEXT,  -- stable Legal Surface Graph node identity
    label                   TEXT,  -- short display surface
    payload_json            TEXT,  -- typed surface facts
    links_json              TEXT,  -- co-located overlay/node affordances
    overlay_status          TEXT,  -- resolution status (reference/term_use only)
    source_span_byte_offset INTEGER,
    source_span_byte_len     INTEGER,
    rendered_statute_id     TEXT,
    rendered_effective_date TEXT,
    rendered_address        TEXT,
    rendered_segment_index  INTEGER,
    rendered_char_start     INTEGER,
    rendered_char_end       INTEGER
);
"""


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ExportStats:
    statute_id: str
    title: str
    slice_prefix: str
    granularity: str
    n_change_dates: int
    n_transitions: int
    n_content_blobs: int
    n_content_blob_inserts_attempted: int
    n_checkpoints: int
    n_active_at_rows: int
    n_display_nodes: int
    n_source_artifacts: int
    n_edges: int
    n_evidence_events: int
    n_lawvm_interlinks: int
    n_lawvm_interlink_targets: int
    n_lawvm_surface_overlays: int
    n_derivation_edges: int
    db_path: str
    db_size_bytes: int
    replay_seconds: float

    @property
    def dedup_ratio(self) -> float:
        if self.n_content_blob_inserts_attempted == 0:
            return 0.0
        return 1.0 - (self.n_content_blobs / self.n_content_blob_inserts_attempted)


def _matches_slice(address: str, slice_prefix: str) -> bool:
    if not slice_prefix:
        return True
    return address == slice_prefix or address.startswith(slice_prefix + "/")


class _OracleReadMemoizingCorpus:
    """Delegating corpus proxy that memoizes per-statute consolidated reads.

    Target-preview enrichment resolves one row per unique ``target_key``, and a
    target key embeds the *locator* (``section:5`` vs ``section:12``). Many keys
    therefore point at the SAME target statute, and each formerly triggered a
    fresh ``read_oracle`` of that statute's full consolidated XML — an N+1 read
    over the corpus (measured: 291 reads, only 76 distinct statutes; ~75% pure
    re-reads dominating export wall-clock).

    ``read_oracle``/``read_source`` return the cached PIT consolidated artifact,
    which the store treats as immutable for the duration of a run (see
    ``TransparentStore.read_oracle``); memoizing them by ``sid`` returns the
    byte-identical payload, so the exported DB is unchanged. The cache is
    process-local, lives only for one export, and is bounded by the number of
    distinct cited target statutes (the corpus already holds these bytes), so it
    adds no unbounded memory pressure. Every other corpus method is delegated
    untouched.
    """

    __slots__ = ("_corpus", "_oracle_cache", "_source_cache")

    def __init__(self, corpus: Any) -> None:
        self._corpus = corpus
        self._oracle_cache: Dict[str, Any] = {}
        self._source_cache: Dict[str, Any] = {}

    def read_oracle(self, sid: str) -> Any:
        cache = self._oracle_cache
        if sid not in cache:
            cache[sid] = self._corpus.read_oracle(sid)
        return cache[sid]

    def read_source(self, sid: str) -> Any:
        cache = self._source_cache
        if sid not in cache:
            cache[sid] = self._corpus.read_source(sid)
        return cache[sid]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._corpus, name)


def export_transition_graph(
    statute_id: str,
    out_path: str | Path,
    slice_prefix: str = "",
    *,
    granularity: str = DEFAULT_GRANULARITY,
    quiet: bool = False,
    profile: TransitionGraphExportProfile | None = None,
    interlink_provider: LawvmInterlinkExportProvider | None = None,
    overlay_provider: LawvmSurfaceOverlayExportProvider | None = None,
    replay_runner: Any | None = None,
    tree_materializer: Any | None = None,
    derivation_provider: Any | None = None,
) -> ExportStats:
    """Export the certified transition graph for ``statute_id`` to ``out_path``.

    ``statute_id`` may be either canonical 'num/year' (e.g. "301/2004") or
    engine 'year/num' (e.g. "2004/301"); both are accepted. ``slice_prefix`` is
    an optional address-prefix filter (e.g. "chapter:11"); empty = whole act.
    ``granularity`` selects the covering-frontier depth ("subsection" default,
    "section", or legacy "chapter"); see :func:`covering_units`.
    Neutral LawVM interlinks are always projected into ``lawvm_interlinks``;
    legal-reference recognition must happen in LawVM, never in the viewer.

    ``derivation_provider`` (optional) is a callable
    ``(canonical_id, corpus, lo_ops) -> DerivationEdgeSet`` that projects the
    jurisdiction's typed reference/derivation edges (textual | model_code |
    conformance | citation) for the statute. When supplied, its edges are flushed
    to the ``derivation_edges`` table so the FI reference-edge extraction reaches
    the exported product surface; when ``None`` (the default) that table is empty
    and every other surface is unchanged.
    """
    export_profile = profile or _default_export_profile()
    canonical_id = export_profile.canonical_statute_id(statute_id)
    engine_id = export_profile.engine_statute_id(canonical_id)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    t0 = time.time()
    if not quiet:
        print(f"[export] replaying {engine_id} (engine authority)...", flush=True)
    run_replay = replay_runner or run_engine_replay
    materialize_tree = tree_materializer or materialize_oracle_tree
    bundle = dataclasses.replace(
        run_replay(engine_id, profile=export_profile),
        statute_id=canonical_id,
        engine_id=engine_id,
    )
    replay_seconds = time.time() - t0
    if not quiet:
        print(
            f"[export] replay done in {replay_seconds:.1f}s: "
            f"{len(bundle.lo_ops)} L2 ops, {len(bundle.timelines)} timelines, "
            f"{len(bundle.change_dates)} change-dates",
            flush=True,
        )

    ops_by_date = _index_ops_by_date(bundle.lo_ops)
    expiry_ops_by_date = _index_ops_by_expiry_date(bundle.lo_ops)

    conn = sqlite3.connect(str(out_path))
    try:
        # The export DB is a freshly generated artifact; if the process is
        # interrupted, callers rerun the export rather than recovering partial
        # writes. Keep durability overhead out of the hot path.
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.executescript(_SCHEMA)

        blob_hashes: set[str] = set()
        blob_inserts_attempted = 0

        def _store_blob(node: IRNode) -> str:
            nonlocal blob_inserts_attempted
            h = structural_subtree_hash(node)
            blob_inserts_attempted += 1
            if h not in blob_hashes:
                blob_hashes.add(h)
                conn.execute(
                    "INSERT OR IGNORE INTO content_blobs(content_hash, content_json) VALUES (?, ?)",
                    (h, _subtree_json(node)),
                )
            return h

        # --- materialize the oracle tree at every change-date ---
        # We track the live covering set (document-ordered top-level units) per
        # date and diff consecutive dates into L3 transitions. The covering set
        # reconstructs the whole (sliced) tree with no overlap, so a JS reducer
        # folding these transitions can rebuild + hash the full tree.
        #
        # ``cur_state`` maps covering-address -> subtree_hash.
        # ``cur_order`` is the document-ordered covering-address list.
        prev_state: Dict[str, str] = {}
        cur_order: List[str] = []
        checkpoint_rows: List[CheckpointRow] = []
        # active_rows are appended in document order so SQLite rowid preserves it.
        active_rows: List[ActiveAtRow] = []
        display_rows: List[DisplayNodeRow] = []
        transition_rows: List[TransitionRow] = []
        segments_by_date: dict[str, list[RenderedTextSegment]] = {}
        # Covering addresses are meant to be unique within a date's covering set.
        # When two distinct nodes tile to the same address (a structural pathology
        # seen in some UK acts, e.g. a duplicated subsection number), the verifiable
        # surfaces — transitions, active_at, and the browser fold — all dedupe by
        # address (last-in-document-order wins). The certified checkpoint MUST hash
        # that same deduped set or it could never match a fold; we collect the
        # colliding addresses so the silent dedup is surfaced, never hidden.
        address_collisions: set[str] = set()
        seq = 0

        for date in bundle.change_dates:
            tree = materialize_tree(bundle, date)
            segments_by_date[date] = rendered_text_segments(date, tree, slice_prefix)
            units = covering_units(tree, slice_prefix, granularity)
            active_addresses = frozenset(addr for addr, _node in units)
            display_rows.extend(
                display_node_rows(date, tree, slice_prefix, active_addresses=active_addresses)
            )
            cur_state = {}
            cur_order = []
            for addr, node in units:
                h = _store_blob(node)
                if addr in cur_state:
                    address_collisions.add(addr)
                cur_state[addr] = h
                cur_order.append(addr)
                active_rows.append(ActiveAtRow(date=date, address=addr, content_hash=h))

            # Certified checkpoint hash over the deduped covering set (keyed by
            # address), matching exactly what the transition stream reconstructs
            # and the browser folds — reproducible_tree_hash sorts by address, so
            # document order is irrelevant to the hash and preserved separately
            # via active_at rowid order.
            tree_hash = reproducible_tree_hash(list(cur_state.items()))
            checkpoint_rows.append(
                CheckpointRow(
                    date=date,
                    address_prefix=slice_prefix,
                    tree_hash=tree_hash,
                    active_node_count=len(cur_state),
                )
            )

            # --- diff prev -> cur into L3 transitions (in document order) ---
            all_addrs = list(dict.fromkeys(list(prev_state.keys()) + cur_order))
            for addr in all_addrs:
                pre = prev_state.get(addr, "")
                post = cur_state.get(addr, "")
                if pre == post:
                    continue
                seq += 1
                transition_id = f"t{seq:06d}:{date}:{addr}"
                if pre == "" and post != "":
                    action = "set_subtree"  # newly present (insert or restore)
                elif pre != "" and post == "":
                    action = "delete_subtree"  # gone (repeal/expiry)
                else:
                    action = "set_subtree"  # content changed in place

                payload_hash = post  # the resulting subtree hash

                # L2 annotation for display: any op effective on this date whose
                # target is at or below this covering address — PLUS any op whose
                # fixed-term validity EXPIRES on this date (a temporary act's
                # scheduled lapse drives a real state change here, and the
                # provenance must point at the act that scheduled it, never
                # render as an unexplained deletion/reversion).
                ops = _ops_for_covering(ops_by_date.get(date, []), addr)
                expiring = _ops_for_covering(expiry_ops_by_date.get(date, []), addr)
                kind_set = {str(o.action) for o in ops}
                summaries = [_legal_op_summary(o) for o in ops[:3]]
                src_ids = {
                    export_profile.canonical_statute_id(o.source.statute_id)
                    for o in ops
                    if o.source is not None and o.source.statute_id
                }
                if expiring:
                    kind_set.add("expiry")
                    summaries.extend(f"expiry of {_legal_op_summary(o)}" for o in expiring[:3])
                    src_ids.update(
                        export_profile.canonical_statute_id(o.source.statute_id)
                        for o in expiring
                        if o.source is not None and o.source.statute_id
                    )
                legal_op_kind = ",".join(sorted(kind_set))
                legal_op_summary = " | ".join(summaries[:4])
                source_id = sorted(src_ids)[0] if src_ids else ""

                flags: Dict[str, Any] = {}
                if post == "":
                    flags["removed"] = True
                if pre == "" and post != "":
                    flags["created"] = True
                if expiring and not ops:
                    flags["temporary_expiry"] = True

                transition_rows.append(
                    TransitionRow(
                        transition_id=transition_id,
                        sequence=seq,
                        effective_date=date,
                        expires_date="",
                        action=action,
                        target_address=addr,
                        pre_hash=pre,
                        post_hash=post,
                        payload_hash=payload_hash,
                        legal_op_kind=legal_op_kind,
                        legal_op_summary=legal_op_summary,
                        source_id=source_id,
                        he_ref="",
                        source_ref="",
                        flags=json.dumps(flags, ensure_ascii=False),
                    )
                )

            prev_state = cur_state

        if address_collisions and not quiet:
            sample = ", ".join(sorted(address_collisions)[:8])
            print(
                f"[export] WARNING: {len(address_collisions)} covering address(es) "
                f"collided (two distinct nodes tiled to the same address); the "
                f"certified checkpoint and all verifiable surfaces keep the "
                f"last-in-document-order node and drop the earlier one. This is a "
                f"structural pathology in the materialized tree, not a clean "
                f"covering. Colliding: {sample}",
                flush=True,
            )

        # --- source_artifacts: statute + every amendment referenced by ops ---
        corpus = export_profile.corpus()
        projected_interlinks = (
            interlink_provider.project_interlinks(canonical_id, corpus)
            if interlink_provider is not None
            else []
        )
        target_resolver = None
        if (
            interlink_provider is not None
            and interlink_provider.resolve_target is not None
        ):
            # Target-preview resolution reads one target statute per unique
            # locator key; many keys share a statute, so memoize the consolidated
            # reads for this export to collapse the N+1 (byte-identical output;
            # see _OracleReadMemoizingCorpus).
            preview_context = InterlinkTargetPreviewContext(
                source_statute_id=canonical_id,
                corpus=(
                    _OracleReadMemoizingCorpus(corpus)
                    if corpus is not None
                    else None
                ),
            )
            resolve_target = interlink_provider.resolve_target
            assert resolve_target is not None
            def target_resolver(target_ref: Any) -> Any:
                return resolve_target(
                    target_ref,
                    preview_context,
                )
        interlink_rows, interlink_target_rows = enrich_lawvm_interlink_targets(
            projected_interlinks,
            target_resolver=target_resolver,
        )
        interlink_rows = place_lawvm_interlinks(
            interlink_rows,
            statute_id=canonical_id,
            segments_by_date=segments_by_date,
        )
        interlink_placement_summary = placement_summary(interlink_rows)

        # --- lawvm_surface_overlays: the FULL Legal Surface Graph projection ---
        # The jurisdiction adapter projects whole-body overlay rows (defined
        # terms, frames, temporal markers, references) with null rendered_*; the
        # exporter then places them onto the SAME per-date rendered segments it
        # placed interlinks onto, so the viewer paints overlays and interlinks
        # with identical rendered_address/segment/char coordinates.
        projected_overlays = (
            overlay_provider.project_overlays(canonical_id, corpus)
            if overlay_provider is not None
            else []
        )
        overlay_rows = place_lawvm_surface_overlays(
            projected_overlays,
            statute_id=canonical_id,
            segments_by_date=segments_by_date,
        )

        # --- derivation_edges: typed FI reference/derivation edges ---
        # The jurisdiction's reference-edge extractor is a READ/PUBLISH surface
        # (never a replay input). When wired, it classifies the statute's
        # relationships into the four DISTINCT typed kinds and we carry that
        # typing verbatim into the exported graph.
        derivation_rows: List[DerivationEdgeRow] = []
        if derivation_provider is not None:
            derivation_edge_set = derivation_provider(canonical_id, corpus, bundle.lo_ops)
            if derivation_edge_set is not None:
                derivation_rows = build_derivation_edge_rows(derivation_edge_set)

        source_rows: List[SourceArtifactRow] = []
        source_ref_by_amendment: Dict[str, str] = {}
        # the base statute
        source_rows.append(
            SourceArtifactRow(
                source_id=canonical_id,
                kind="statute",
                canonical_id=canonical_id,
                title=bundle.title,
                url=export_profile.statute_url(canonical_id, engine_id),
                content_hash="",
                date="",
            )
        )
        amendment_meta: Dict[str, Tuple[str, str]] = {}  # canonical -> (title, enacted)
        for op in bundle.lo_ops:
            src = op.source
            if src is None or not src.statute_id:
                continue
            canon = export_profile.canonical_statute_id(src.statute_id)
            if canon == canonical_id:
                continue
            if canon not in amendment_meta:
                amendment_meta[canon] = (src.title or "", src.enacted or src.effective or "")
        for canon, (title, date) in sorted(amendment_meta.items()):
            engine_amd = export_profile.engine_statute_id(canon)
            source_ref = export_profile.source_reference(corpus, engine_amd) if corpus is not None else ""
            source_ref_by_amendment[canon] = source_ref
            url = export_profile.amendment_url(canon, engine_amd)
            source_rows.append(
                SourceArtifactRow(
                    source_id=canon,
                    kind="amendment",
                    canonical_id=canon,
                    title=title,
                    url=url,
                    content_hash="",
                    date=date,
                )
            )

        conn.executemany(
            "INSERT OR REPLACE INTO source_artifacts"
            "(source_id, kind, canonical_id, title, url, content_hash, date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in source_rows],
        )

        # Backfill jurisdiction-owned source references onto transition rows.
        # ``he_ref`` is kept as a legacy mirror for existing FI viewer data.
        transition_rows = [
            row.with_source_ref(source_ref_by_amendment.get(row.source_id, ""))
            for row in transition_rows
        ]

        conn.executemany(
            "INSERT INTO transitions"
            "(transition_id, sequence, effective_date, expires_date, action, "
            " target_address, pre_hash, post_hash, payload_hash, legal_op_kind, "
            " legal_op_summary, source_id, he_ref, source_ref, flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in transition_rows],
        )
        conn.executemany(
            "INSERT INTO checkpoints(date, address_prefix, tree_hash, active_node_count) VALUES (?, ?, ?, ?)",
            [row.sql_values() for row in checkpoint_rows],
        )
        # set transition_id on active_at where a transition occurred at that date+addr
        trans_by_date_addr: Dict[Tuple[str, str], str] = {}
        for row in transition_rows:
            trans_by_date_addr[(row.effective_date, row.target_address)] = row.transition_id
        active_rows = [
            row.with_transition_id(trans_by_date_addr.get((row.date, row.address), ""))
            for row in active_rows
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO active_at(date, address, content_hash, transition_id) VALUES (?, ?, ?, ?)",
            [row.sql_values() for row in active_rows],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO display_nodes(date, address, kind, label, num, heading) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in display_rows],
        )

        # --- edges: created_by / amended_by (address-version -> source) and
        #     supersedes (transition -> transition at same address) ---
        edge_rows: List[EdgeRow] = []
        eid = 0
        # created_by / amended_by
        for row in transition_rows:
            transition_id = row.transition_id
            addr = row.target_address
            source_id = row.source_id
            flags = json.loads(row.flags) if row.flags else {}
            if not source_id:
                continue
            kind = "created_by" if flags.get("created") else "amended_by"
            eid += 1
            edge_rows.append(
                EdgeRow(
                    edge_id=f"e{eid:06d}",
                    kind=kind,
                    from_id=transition_id,
                    to_id=source_id,
                    payload=json.dumps({"address": addr}),
                )
            )
        # supersedes: consecutive transitions at the same address
        by_addr: Dict[str, List[TransitionRow]] = {}
        for row in transition_rows:
            by_addr.setdefault(row.target_address, []).append(row)
        for addr, rows in by_addr.items():
            rows_sorted = sorted(rows, key=lambda r: r.sequence)
            for a, b in zip(rows_sorted, rows_sorted[1:], strict=False):
                eid += 1
                edge_rows.append(
                    EdgeRow(
                        edge_id=f"e{eid:06d}",
                        kind="supersedes",
                        from_id=b.transition_id,
                        to_id=a.transition_id,
                        payload=json.dumps({"address": addr}),
                    )
                )
        conn.executemany(
            "INSERT INTO edges(edge_id, kind, from_id, to_id, payload) VALUES (?, ?, ?, ?, ?)",
            [row.sql_values() for row in edge_rows],
        )

        evidence_rows = build_evidence_event_rows(bundle, profile=export_profile)
        conn.executemany(
            "INSERT INTO evidence_events"
            "(event_id, surface, kind, role, severity, phase, source_id, "
            " effective_date, target_address, rule_id, title, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in evidence_rows],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO lawvm_interlinks"
            "(interlink_id, source_jurisdiction, source_work_kind, source_local_id, "
            " source_work_id, source_locator, surface_text, surface_kind, role, "
            " target_jurisdiction, target_work_kind, target_local_id, target_work_id, "
            " target_locator, target_url, candidate_work_ids, resolution_status, "
            " confidence, resolver_id, source_artifact_id, source_span_byte_offset, "
            " source_span_byte_len, rendered_statute_id, rendered_effective_date, "
            " rendered_address, rendered_segment_index, rendered_char_start, "
            " rendered_char_end, valid_at_start, valid_at_end, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in interlink_rows],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO lawvm_interlink_targets"
            "(target_key, target_jurisdiction, target_work_kind, target_local_id, "
            " target_work_id, target_locator, target_url, target_links_json, "
            " preview_status, preview_source, title, locator_label, hierarchy_json, "
            " preview_text, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in interlink_target_rows],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO lawvm_surface_overlays("
            + ", ".join(SURFACE_OVERLAY_ROW_COLUMNS)
            + ") VALUES ("
            + ", ".join("?" for _ in SURFACE_OVERLAY_ROW_COLUMNS)
            + ")",
            [overlay_row_sql_values(row) for row in overlay_rows],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO derivation_edges"
            "(edge_id, derivation_kind, relation_kind, authority_plane, source_ref, "
            " target_ref, replay_authorized, edge_status, edge_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row.sql_values() for row in derivation_rows],
        )

        # --- meta ---
        meta_rows = {
            "statute_id": canonical_id,
            "title": bundle.title,
            "slice": slice_prefix or None,
            "granularity": granularity,
            # Certification vs localization provenance (viewer contract):
            # transitions are CERTIFIED at the covering-frontier granularity;
            # any finer-grained change attribution a consumer renders is DERIVED
            # by diffing the certified pre/post subtrees, and must be labelled
            # as such, never presented as engine certification.
            "certification_granularity": granularity,
            "localization_granularity": "node",
            "localization_status": "derived_from_certified_subtree_diff",
            # Node addresses come from engine-exported labels/nums, never from
            # positional counters in the consumer.
            "node_address_source": "exported",
            "jurisdiction": export_profile.jurisdiction,
            "lang": export_profile.lang,
            "schema_version": SCHEMA_VERSION,
            "change_dates": bundle.change_dates,
            # Placement-v0 regression signal: per-status counts over distinct
            # source occurrences (range/coordination grouped to ONE occurrence).
            "interlink_placement_summary": interlink_placement_summary,
            "generated_note": (
                "Certified transition graph exported by LawVM "
                "export_transition_graph (Design D). The Python replay engine is "
                "the only authority; checkpoints/active_at are engine-authored "
                "oracles. L3 transitions carry pre/post structural hashes for "
                "browser-side certified folding; L2 legal_op_* fields are "
                "display-only."
            ),
        }
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta_rows.items()],
        )

        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()

    # --- L2 sidecar for independent browser-side replay (Exp-2) ---
    sidecar = emit_l2_sidecar(bundle, checkpoint_rows, profile=export_profile)
    sidecar_path = out_path.with_suffix(out_path.suffix + ".l2.json")
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    db_size = out_path.stat().st_size
    stats = ExportStats(
        statute_id=canonical_id,
        title=bundle.title,
        slice_prefix=slice_prefix,
        granularity=granularity,
        n_change_dates=len(bundle.change_dates),
        n_transitions=len(transition_rows),
        n_content_blobs=len(blob_hashes),
        n_content_blob_inserts_attempted=blob_inserts_attempted,
        n_checkpoints=len(checkpoint_rows),
        n_active_at_rows=len(active_rows),
        n_display_nodes=len(display_rows),
        n_source_artifacts=len(source_rows),
        n_edges=len(edge_rows),
        n_evidence_events=len(evidence_rows),
        n_lawvm_interlinks=len(interlink_rows),
        n_lawvm_interlink_targets=len(interlink_target_rows),
        n_lawvm_surface_overlays=len(overlay_rows),
        n_derivation_edges=len(derivation_rows),
        db_path=str(out_path),
        db_size_bytes=db_size,
        replay_seconds=replay_seconds,
    )
    return stats


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    from lawvm.tools.transition_graph_jurisdictions import (
        transition_graph_adapter_for_jurisdiction,
    )

    statute = getattr(args, "statute", None)
    out = getattr(args, "out", None)
    slice_prefix = getattr(args, "slice", "") or ""
    granularity = getattr(args, "granularity", DEFAULT_GRANULARITY) or DEFAULT_GRANULARITY
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    if not statute or not out:
        print("error: --statute and --out are required", flush=True)
        raise SystemExit(2)
    try:
        adapter = transition_graph_adapter_for_jurisdiction(jurisdiction)
    except ValueError as exc:
        print(f"error: {exc}", flush=True)
        raise SystemExit(2) from exc
    stats = export_transition_graph(
        statute,
        out,
        slice_prefix,
        granularity=granularity,
        quiet=False,
        profile=adapter.profile,
        interlink_provider=adapter.interlink_provider,
        overlay_provider=adapter.overlay_provider,
        replay_runner=adapter.replay_runner,
        tree_materializer=adapter.tree_materializer,
    )
    print("", flush=True)
    print(f"  statute:          {stats.statute_id}  ({stats.title})", flush=True)
    print(f"  slice:            {stats.slice_prefix or '<whole act>'}", flush=True)
    print(f"  granularity:      {stats.granularity}", flush=True)
    print(f"  db path:          {stats.db_path}", flush=True)
    print(f"  db size:          {stats.db_size_bytes / 1024 / 1024:.2f} MB", flush=True)
    print(f"  change_dates:     {stats.n_change_dates}", flush=True)
    print(f"  transitions:      {stats.n_transitions}", flush=True)
    print(
        f"  content_blobs:    {stats.n_content_blobs} "
        f"(of {stats.n_content_blob_inserts_attempted} stored attempts; "
        f"dedup ratio {stats.dedup_ratio:.1%})",
        flush=True,
    )
    print(f"  checkpoints:      {stats.n_checkpoints}", flush=True)
    print(f"  active_at rows:   {stats.n_active_at_rows}", flush=True)
    print(f"  display_nodes:    {stats.n_display_nodes}", flush=True)
    print(f"  source_artifacts: {stats.n_source_artifacts}", flush=True)
    print(f"  edges:            {stats.n_edges}", flush=True)
    print(f"  evidence_events:  {stats.n_evidence_events}", flush=True)
    print(f"  lawvm_interlinks: {stats.n_lawvm_interlinks}", flush=True)
    print(f"  interlink_targets: {stats.n_lawvm_interlink_targets}", flush=True)
    print(f"  surface_overlays: {stats.n_lawvm_surface_overlays}", flush=True)
    print(f"  derivation_edges: {stats.n_derivation_edges}", flush=True)
    print(f"  replay seconds:   {stats.replay_seconds:.1f}", flush=True)
