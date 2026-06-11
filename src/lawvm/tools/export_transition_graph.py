"""Export a certified transition graph for a Finnish statute (Design D).

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
(statute ``"301/2004"``, amendment ``"YYYY/N"`` säädös ids, HE ids, address
strings); content subtrees are de-duplicated by sha256.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.ir import IRNode, LegalAddress

SCHEMA_VERSION = "transition-graph.v1"

# Base-version sentinel effective date used by compile_timelines for the
# original (unamended) provision content. It is not a real calendar change-date.
_BASE_SENTINEL_DATE = "0000-00-00"

# HE (hallituksen esitys) reference markup in amendment AKN XML:
#   /akn/fi/doc/government-proposal/YEAR/NUMBER  ... >HE 46/2006</ref>
_HE_HREF_RE = re.compile(r"/akn/fi/doc/government-proposal/(\d{4})/(\d{1,4}-\d{1,4}|\d{1,4})")
_HE_TEXT_RE = re.compile(r"\bHE\s{1,4}(\d{1,4}-\d{1,4}|\d{1,4})/(\d{4})\s{0,4}vp", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Structural subtree hashing (L3 certification primitive)
# ---------------------------------------------------------------------------


# Canonical implementation lives in lawvm.core.ir_helpers so the apply-time
# WriteReceipt producer and this exporter share the single frozen recipe
# (CERTIFIED_TREE_TRANSITION_TRACE_V0.md §2.2). Re-exported here because this
# module historically owned it.
from lawvm.core.ir_helpers import structural_subtree_hash as structural_subtree_hash  # noqa: E402


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


def _iter_addressed_nodes(
    root: IRNode,
    prefix: Tuple[Tuple[str, str], ...] = (),
) -> List[Tuple[str, IRNode]]:
    """Yield (address_string, node) for every labeled descendant of ``root``.

    The root body itself is unlabeled and skipped; labeled structural nodes
    (chapters, sections, subsections, ...) become addressable rows. Returned in
    document order.
    """
    out: List[Tuple[str, IRNode]] = []
    for child in root.children:
        kind = str(child.kind)
        label = child.label or ""
        if label:
            path = prefix + ((kind, label),)
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
        for child in node.children:
            kind = str(child.kind)
            label = child.label or ""
            if label:
                path = prefix + ((kind, label),)
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


def compute_change_dates(timelines: Dict[LegalAddress, Any]) -> List[str]:
    """Return the sorted list of real calendar change-dates from timelines.

    The union of every version effective/expires date, excluding the
    ``0000-00-00`` base sentinel and empty strings. An expiry date D is itself
    a change-date: the provision is gone on/after D, so the tree at D differs.
    """
    dates: set[str] = set()
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
    return out


# ---------------------------------------------------------------------------
# Engine invocation + per-date oracle materialization
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ReplayBundle:
    """Everything captured from a single authoritative engine replay."""

    statute_id: str  # canonical "301/2004"
    engine_id: str  # engine-facing "2004/301" (drives op-target/timeline keying)
    title: str
    result: Any  # ReplayResult
    lo_ops: List[Any]
    timelines: Dict[LegalAddress, Any]
    change_dates: List[str]


def run_engine_replay(statute_id_yearnum: str) -> ReplayBundle:
    """Run the Finland engine once and capture the L2 ops + timelines.

    ``statute_id_yearnum`` is the engine-facing "YYYY/N" id (e.g. "2004/301").
    The replay is materialized at the latest change-date so the full op stream
    and timeline graph are available for re-materialization at earlier dates.
    """
    from lawvm.finland.grafter import replay_xml

    lo_ops: List[Any] = []
    # Materialize far in the future first to collect the complete timeline set.
    far_result = replay_xml(
        statute_id_yearnum,
        mode="legal_pit",
        as_of="9999-12-31",
        lo_ops_out=lo_ops,
        quiet=True,
    )
    timelines = far_result.timelines or {}
    change_dates = compute_change_dates(timelines)
    return ReplayBundle(
        statute_id=_canonical_statute_id(statute_id_yearnum),
        engine_id=statute_id_yearnum,
        title=far_result.title,
        result=far_result,
        lo_ops=lo_ops,
        timelines=timelines,
        change_dates=change_dates,
    )


def _op_variant_kind(op: Any) -> str:
    """Return 'temporary' when the op carries a source-side expiry, else 'permanent'."""
    src = op.source
    if src is not None and (src.expires or ""):
        return "temporary"
    return "permanent"


def emit_l2_sidecar(bundle: ReplayBundle, checkpoints: List[Tuple[str, str, str, int]]) -> Dict[str, Any]:
    """Build the JSON sidecar for independent browser-side L2 replay (Exp-2).

    Carries the base body tree and the full resolved L2 operation stream with
    the temporal/structural fields a JS folder needs: effective/expires dates,
    same-day ``sequence``, action, target/destination/anchor addresses, payload
    subtree, and variant kind. Plus the engine oracle checkpoint hashes so the
    JS folder can self-score WITHOUT consulting the certified transition graph.
    """
    result = bundle.result
    base_body = result.ctx.base_ir  # IRNode body of the unamended statute
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
                "source_statute": _canonical_statute_id(src.statute_id) if src is not None and src.statute_id else "",
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
            "source_statute": _canonical_statute_id(me.source_statute) if me.source_statute else "",
        }
        for me in bundle.result.products.migration_events
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
            {"date": d, "tree_hash": th, "active_node_count": cnt} for (d, _prefix, th, cnt) in checkpoints
        ],
    }


def materialize_oracle_tree(bundle: ReplayBundle, as_of: str) -> IRNode:
    """Re-materialize the engine's authoritative PIT tree at ``as_of``.

    Uses the engine's own ``build_replay_products`` with the legal_pit profile
    settings (validated to match a full ``replay_xml(as_of=...)`` exactly,
    including temporary-provision expiry/reversion). This is the ORACLE.
    """
    from lawvm.finland.replay_products import build_replay_products

    result = bundle.result
    products = build_replay_products(
        ctx=result.ctx,
        statute_id=bundle.engine_id,
        replay_fold_state=result.products.replay_fold_state,
        lo_ops_out=bundle.lo_ops,
        as_of=as_of,
        expires_as_of=as_of,
        synthesize_repeal_placeholders=True,
        temporal_events=result.products.temporal_events,
        migration_events=result.products.migration_events,
    )
    return products.materialized_state.ir


# ---------------------------------------------------------------------------
# Canonical id helpers
# ---------------------------------------------------------------------------


_MIN_PLAUSIBLE_YEAR = 1734
_MAX_PLAUSIBLE_YEAR = 2200


def _is_plausible_year(token: str) -> bool:
    return len(token) == 4 and token.isdigit() and _MIN_PLAUSIBLE_YEAR <= int(token) <= _MAX_PLAUSIBLE_YEAR


def _split_year_num(statute_id: str) -> Optional[Tuple[str, str]]:
    """Return (year, num) for a 'year/num' or 'num/year' id, else None.

    Disambiguates by which component is a plausible 4-digit year (1734..2200).
    When both look like years (rare, pathological), prefers the engine ordering
    where the year is first.
    """
    parts = statute_id.strip().split("/")
    if len(parts) != 2:
        return None
    a, b = parts[0], parts[1]
    a_year = _is_plausible_year(a)
    b_year = _is_plausible_year(b)
    if a_year and not b_year:
        return a, b  # 'year/num'
    if b_year and not a_year:
        return b, a  # 'num/year' -> swap to (year, num)
    if a_year and b_year:
        return a, b  # ambiguous: assume engine 'year/num'
    return None


def _canonical_statute_id(statute_id: str) -> str:
    """Return the canonical GLOBAL statute id in 'num/year' form ('301/2004').

    The engine uses 'year/num' internally; the canonical cross-referenceable id
    used in the export and the user-facing CLI uses 'num/year' (the säädös id).
    """
    yn = _split_year_num(statute_id)
    if yn is None:
        return statute_id.strip()
    year, num = yn
    return f"{num}/{year}"


def _engine_statute_id(statute_id: str) -> str:
    """Return the engine-facing 'year/num' form from a canonical 'num/year'."""
    yn = _split_year_num(statute_id)
    if yn is None:
        return statute_id.strip()
    year, num = yn
    return f"{year}/{num}"


# ---------------------------------------------------------------------------
# HE reference extraction (best effort, from amendment AKN XML)
# ---------------------------------------------------------------------------


def _extract_he_ref(amendment_xml: Optional[bytes]) -> str:
    """Return the first HE ref (e.g. "HE 46/2006 vp") in the amendment, or ""."""
    if not amendment_xml:
        return ""
    try:
        text = amendment_xml.decode("utf-8", "ignore")
    except Exception:
        return ""
    m = _HE_HREF_RE.search(text)
    if m:
        return f"HE {m.group(2)}/{m.group(1)} vp"
    m2 = _HE_TEXT_RE.search(text)
    if m2:
        return f"HE {m2.group(1)}/{m2.group(2)} vp"
    return ""


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
    he_ref          TEXT,
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
CREATE INDEX idx_transitions_date ON transitions(effective_date);
CREATE INDEX idx_transitions_addr ON transitions(target_address);
CREATE INDEX idx_active_at_addr ON active_at(address);
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
    n_source_artifacts: int
    n_edges: int
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


def export_transition_graph(
    statute_id: str,
    out_path: str | Path,
    slice_prefix: str = "",
    *,
    granularity: str = DEFAULT_GRANULARITY,
    quiet: bool = False,
) -> ExportStats:
    """Export the certified transition graph for ``statute_id`` to ``out_path``.

    ``statute_id`` may be either canonical 'num/year' (e.g. "301/2004") or
    engine 'year/num' (e.g. "2004/301"); both are accepted. ``slice_prefix`` is
    an optional address-prefix filter (e.g. "chapter:11"); empty = whole act.
    ``granularity`` selects the covering-frontier depth ("subsection" default,
    "section", or legacy "chapter"); see :func:`covering_units`.
    """
    canonical_id = _canonical_statute_id(statute_id)
    engine_id = _engine_statute_id(canonical_id)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    t0 = time.time()
    if not quiet:
        print(f"[export] replaying {engine_id} (engine authority)...", flush=True)
    bundle = run_engine_replay(engine_id)
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
        checkpoint_rows: List[Tuple[str, str, str, int]] = []
        # active_rows are appended in document order so SQLite rowid preserves it.
        active_rows: List[Tuple[str, str, str, str]] = []
        transition_rows: List[tuple] = []
        seq = 0

        for date in bundle.change_dates:
            tree = materialize_oracle_tree(bundle, date)
            units = covering_units(tree, slice_prefix, granularity)
            cur_state = {}
            cur_order = []
            ordered_unit_hashes: List[Tuple[str, str]] = []
            for addr, node in units:
                h = _store_blob(node)
                cur_state[addr] = h
                cur_order.append(addr)
                ordered_unit_hashes.append((addr, h))
                active_rows.append((date, addr, h, ""))

            # certified checkpoint hash over the document-ordered covering set
            tree_hash = reproducible_tree_hash(ordered_unit_hashes)
            checkpoint_rows.append((date, slice_prefix, tree_hash, len(cur_state)))

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
                    _canonical_statute_id(o.source.statute_id)
                    for o in ops
                    if o.source is not None and o.source.statute_id
                }
                if expiring:
                    kind_set.add("expiry")
                    summaries.extend(f"expiry of {_legal_op_summary(o)}" for o in expiring[:3])
                    src_ids.update(
                        _canonical_statute_id(o.source.statute_id)
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
                    (
                        transition_id,
                        seq,
                        date,
                        "",  # expires_date filled below if known
                        action,
                        addr,
                        pre,
                        post,
                        payload_hash,
                        legal_op_kind,
                        legal_op_summary,
                        source_id,
                        "",  # he_ref backfilled after amendment xml lookup
                        json.dumps(flags, ensure_ascii=False),
                    )
                )

            prev_state = cur_state

        # --- source_artifacts: statute + every amendment referenced by ops ---
        from lawvm.finland.corpus import _get_corpus_store

        corpus = _get_corpus_store()

        source_rows: List[tuple] = []
        he_by_amendment: Dict[str, str] = {}
        # the base statute
        source_rows.append(
            (
                canonical_id,
                "statute",
                canonical_id,
                bundle.title,
                f"https://www.finlex.fi/fi/laki/ajantasa/{engine_id.split('/')[0]}/{engine_id.split('/')[1]}",
                "",
                "",
            )
        )
        amendment_meta: Dict[str, Tuple[str, str]] = {}  # canonical -> (title, enacted)
        for op in bundle.lo_ops:
            src = op.source
            if src is None or not src.statute_id:
                continue
            canon = _canonical_statute_id(src.statute_id)
            if canon == canonical_id:
                continue
            if canon not in amendment_meta:
                amendment_meta[canon] = (src.title or "", src.enacted or src.effective or "")
        for canon, (title, date) in sorted(amendment_meta.items()):
            engine_amd = _engine_statute_id(canon)
            he_ref = ""
            try:
                amd_xml = corpus.read_amendment(engine_amd)
                he_ref = _extract_he_ref(amd_xml)
            except Exception:
                he_ref = ""
            he_by_amendment[canon] = he_ref
            yr, num = engine_amd.split("/") if "/" in engine_amd else ("", "")
            url = f"https://www.finlex.fi/fi/laki/alkup/{yr}/{engine_amd.replace('/', '')}" if yr else ""
            source_rows.append((canon, "amendment", canon, title, url, "", date))

        conn.executemany(
            "INSERT OR REPLACE INTO source_artifacts"
            "(source_id, kind, canonical_id, title, url, content_hash, date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r[0], r[1], r[2], r[3], r[4], r[5], r[6]) if len(r) == 7 else r for r in source_rows],
        )

        # backfill he_ref onto transition rows
        transition_rows = [row[:12] + (he_by_amendment.get(row[11], ""),) + row[13:] for row in transition_rows]

        conn.executemany(
            "INSERT INTO transitions"
            "(transition_id, sequence, effective_date, expires_date, action, "
            " target_address, pre_hash, post_hash, payload_hash, legal_op_kind, "
            " legal_op_summary, source_id, he_ref, flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            transition_rows,
        )
        conn.executemany(
            "INSERT INTO checkpoints(date, address_prefix, tree_hash, active_node_count) VALUES (?, ?, ?, ?)",
            checkpoint_rows,
        )
        # set transition_id on active_at where a transition occurred at that date+addr
        trans_by_date_addr: Dict[Tuple[str, str], str] = {}
        for row in transition_rows:
            trans_by_date_addr[(row[2], row[5])] = row[0]
        active_rows = [(d, a, h, trans_by_date_addr.get((d, a), "")) for (d, a, h, _t) in active_rows]
        conn.executemany(
            "INSERT OR REPLACE INTO active_at(date, address, content_hash, transition_id) VALUES (?, ?, ?, ?)",
            active_rows,
        )

        # --- edges: created_by / amended_by (address-version -> source) and
        #     supersedes (transition -> transition at same address) ---
        edge_rows: List[tuple] = []
        eid = 0
        # created_by / amended_by
        for row in transition_rows:
            transition_id = row[0]
            addr = row[5]
            source_id = row[11]
            flags = json.loads(row[13]) if row[13] else {}
            if not source_id:
                continue
            kind = "created_by" if flags.get("created") else "amended_by"
            eid += 1
            edge_rows.append((f"e{eid:06d}", kind, transition_id, source_id, json.dumps({"address": addr})))
        # supersedes: consecutive transitions at the same address
        by_addr: Dict[str, List[tuple]] = {}
        for row in transition_rows:
            by_addr.setdefault(row[5], []).append(row)
        for addr, rows in by_addr.items():
            rows_sorted = sorted(rows, key=lambda r: r[1])
            for a, b in zip(rows_sorted, rows_sorted[1:], strict=False):
                eid += 1
                edge_rows.append(
                    (
                        f"e{eid:06d}",
                        "supersedes",
                        b[0],
                        a[0],
                        json.dumps({"address": addr}),
                    )
                )
        conn.executemany(
            "INSERT INTO edges(edge_id, kind, from_id, to_id, payload) VALUES (?, ?, ?, ?, ?)",
            edge_rows,
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
            "jurisdiction": "fi",
            "lang": "fi",
            "schema_version": SCHEMA_VERSION,
            "change_dates": bundle.change_dates,
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
    finally:
        conn.close()

    # --- L2 sidecar for independent browser-side replay (Exp-2) ---
    sidecar = emit_l2_sidecar(bundle, checkpoint_rows)
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
        n_source_artifacts=len(source_rows),
        n_edges=len(edge_rows),
        db_path=str(out_path),
        db_size_bytes=db_size,
        replay_seconds=replay_seconds,
    )
    return stats


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    statute = getattr(args, "statute", None)
    out = getattr(args, "out", None)
    slice_prefix = getattr(args, "slice", "") or ""
    granularity = getattr(args, "granularity", DEFAULT_GRANULARITY) or DEFAULT_GRANULARITY
    if not statute or not out:
        print("error: --statute and --out are required", flush=True)
        raise SystemExit(2)
    stats = export_transition_graph(statute, out, slice_prefix, granularity=granularity, quiet=False)
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
    print(f"  source_artifacts: {stats.n_source_artifacts}", flush=True)
    print(f"  edges:            {stats.n_edges}", flush=True)
    print(f"  replay seconds:   {stats.replay_seconds:.1f}", flush=True)
