"""Project the Legal Surface Graph BACK to ``ReferenceMention`` / ``fi_refs`` rows.

Pro r5 Phase 3 / Phase 3b (§D3 "Stage 2 parity projection"). This is the
*reverse* of :mod:`lawvm.finland.legal_surface.lenses.references` (the
``ReferenceLens`` adapter that turns each :class:`ReferenceMention` into
``reference_expr`` + ``reference_resolution`` nodes). Here we walk those nodes
back into ``ReferenceMention`` records and the ``fi_refs`` row representation, so
the graph can be proven equivalent to the existing extractor path (and, in
Stage 3, become the single source of truth that the writers read).

This module is **read-only** with respect to the graph, the extractor, the core
primitives, and the lens. It NEVER edits a writer. It only reconstructs.

────────────────────────────────────────────────────────────────────────────
FULL-ROW PARITY (Phase 3b — the five gaps are now closed)
────────────────────────────────────────────────────────────────────────────

The ``ReferenceLens`` now stashes each mention's AUTHORITATIVE fi_refs fields in
the ``reference_expr`` node payload (rather than re-deriving them from the
char-anchored surface), so this module reconstructs ALL 14 fi_refs columns
byte-identically. The five previously-documented gaps and how they were closed:

  1. SOURCE BYTE SPAN — the graph's ``source_ref`` is a CHAR anchor into
     ``raw_text`` (the graph coordinate, untouched). The mention's authoritative
     BYTE span into ``xml_bytes`` rides the payload as
     ``source_span_{file,byte_offset,len}``. The two coordinate spaces are kept
     distinct: char-coord stays the graph's, byte-origin is reproduced from the
     payload. (Metadata-derived mentions carry None, faithfully reproduced.)

  2. VALIDITY INTERVAL — ``valid_at_start`` / ``valid_at_end`` are carried as ISO
     strings in the payload.

  3. TARGET STAT HASH — ``target_stat_hash`` is carried in the payload.

  4. SOURCE PROVISION REF — the citing provision's full serialized form
     (``source_provision_ref_str``, including the section label, not just the
     work_id) is carried in the payload.

  5. CARDINALITY — every real mention now mints a ``reference_expr`` node, even
     when its surface cannot be char-anchored in ``raw_text`` (the lens uses a
     degenerate-char fallback ``source_ref``; the authoritative byte span still
     rides the payload). No locatable mention is dropped to a residual.

All 14 fi_refs columns therefore round-trip exactly; the parity gate asserts
full-row multiset equality.

See :func:`ROUND_TRIPPABLE_ROW_FIELDS` for the canonical field set used by the
parity gate.
"""
from __future__ import annotations

from datetime import date
from typing import Mapping, Optional

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SurfaceNode,
)
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
    reference_mention_to_row,
)

# ── Parity field sets (the contract the parity gate asserts against) ──────────

#: fi_refs row fields that the graph round-trips byte/field-identically. With the
#: Phase-3b payload enrichment this is now the FULL fi_refs schema.
ROUND_TRIPPABLE_ROW_FIELDS: tuple[str, ...] = (
    "source_statute_id",
    "source_provision_ref_str",
    "target_statute_id",
    "target_provision_ref_str",
    "cite_kind",
    "cite_confidence",
    "edge_subtype",
    "phrase_lemma",
    "source_span_file",
    "source_span_byte_offset",
    "source_span_len",
    "valid_at_start",
    "valid_at_end",
    "target_stat_hash",
)

#: No remaining payload gaps — the lens now stashes every fi_refs field. Kept
#: (empty) so the disjoint/cover test still has a stable symbol to assert
#: against (its presence pins that no field silently slips out of the schema).
PAYLOAD_GAP_ROW_FIELDS: tuple[str, ...] = ()


# ── Inverse enum maps (value string -> enum) ─────────────────────────────────
#
# The lens stored ``cite_kind`` / ``cite_confidence`` as their ``.value``
# strings in the payload. Reverse them via the enum's value lookup — fail loud
# on an unknown value rather than guessing.

_CITE_KIND_BY_VALUE: dict[str, CiteKind] = {k.value: k for k in CiteKind}
_CITE_CONFIDENCE_BY_VALUE: dict[str, CiteConfidence] = {
    c.value: c for c in CiteConfidence
}


def _cite_kind_from_value(value: object) -> CiteKind:
    if not isinstance(value, str) or value not in _CITE_KIND_BY_VALUE:
        raise ValueError(
            f"graph_to_reference_mentions: unknown cite_kind value {value!r}; "
            f"allowed={sorted(_CITE_KIND_BY_VALUE)}"
        )
    return _CITE_KIND_BY_VALUE[value]


def _cite_confidence_from_value(value: object) -> CiteConfidence:
    if not isinstance(value, str) or value not in _CITE_CONFIDENCE_BY_VALUE:
        raise ValueError(
            f"graph_to_reference_mentions: unknown cite_confidence value {value!r}; "
            f"allowed={sorted(_CITE_CONFIDENCE_BY_VALUE)}"
        )
    return _CITE_CONFIDENCE_BY_VALUE[value]


# ── Resolution lookup (reference_expr -> its reference_resolution) ────────────


def _resolution_index(graph: LegalSurfaceGraph) -> dict[str, SurfaceNode]:
    """Map a ``reference_expr`` node_id -> its ``reference_resolution`` node.

    The ``ReferenceLens`` mints one ``resolution_of`` edge (resolution -> expr)
    per mention. We invert it so each expr node can find its resolution payload
    (which carries the ``work_id`` / ``candidates`` resolution outcome).
    """
    resolution_nodes = {
        nid: node
        for nid, node in graph.nodes.items()
        if node.node_kind == "reference_resolution"
    }
    by_expr: dict[str, SurfaceNode] = {}
    for edge in graph.edges:
        if edge.edge_kind != "resolution_of":
            continue
        resolution = resolution_nodes.get(edge.src)
        if resolution is not None:
            by_expr[edge.dst] = resolution
    return by_expr


def _target_ref_from_payload(
    target_id: object,
    serialized: object,
) -> Optional[ProvisionRef]:
    """Rebuild a target :class:`ProvisionRef` from the lens payload.

    The lens stored two fields per expr node:
      * ``target_id``             — the canonical statute id (e.g. ``1982/633``);
      * ``target_provision_ref``  — ``ProvisionRef.serialized()``
                                    (``statute_id[/section[/subsection[/item]]]``).

    The statute id ITSELF contains a ``/`` (``YEAR/NUMBER``), so the serialized
    string cannot be split on ``/`` blindly — and the segment after the section
    may be a section letter-suffix (``2a``), not a momentti integer. We therefore
    use ``target_id`` as the authoritative statute-id boundary, strip exactly
    that prefix off the serialized string, and thread the remaining ``/``-joined
    tail (section / subsection / item) so that ``serialized()`` reproduces the
    stored ``target_provision_ref`` string byte-identically — which is the
    ``target_provision_ref_str`` row field.

    Returns None when the payload names no target (``target_id`` is None) —
    matching the extractor's typed-absent target for UNRESOLVED/BROKEN/OPEN.
    """
    if target_id is None:
        return None
    if not isinstance(target_id, str):
        raise ValueError(
            f"graph_to_reference_mentions: target_id must be a str, got {target_id!r}"
        )
    if not isinstance(serialized, str) or not serialized:
        # Target id present but no serialized provision string: statute-level
        # target with no provision tail.
        return ProvisionRef(statute_id=target_id, provision_path="")

    # Strip the statute-id prefix to isolate the provision tail. The lens always
    # serialized statute_id first, so a mismatch is a real corruption (fail loud).
    if serialized == target_id:
        tail = ""
    elif serialized.startswith(target_id + "/"):
        tail = serialized[len(target_id) + 1 :]
    else:
        raise ValueError(
            "graph_to_reference_mentions: target_provision_ref "
            f"{serialized!r} does not start with target_id {target_id!r}"
        )

    section_label = ""
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None
    if tail:
        tail_parts = tail.split("/")
        section_label = tail_parts[0]
        if len(tail_parts) > 1:
            subsection_num = int(tail_parts[1])
        if len(tail_parts) > 2:
            item_label = tail_parts[2]

    return ProvisionRef(
        statute_id=target_id,
        provision_path="",
        section_label=section_label,
        subsection_num=subsection_num,
        item_label=item_label,
    )


def _source_ref_from_payload(serialized: object) -> ProvisionRef:
    """Rebuild the SOURCE :class:`ProvisionRef` from its serialized payload field.

    The lens stashes ``source_provision_ref`` = ``ProvisionRef.serialized()`` of
    the citing provision (statute id + optional section/subsection/item). We
    rebuild it so ``serialized()`` reproduces ``source_provision_ref_str`` byte-
    identically. The statute id itself contains a ``/`` (``YEAR/NUMBER`` or
    ``NUMBER/YEAR``), so the first TWO ``/``-segments form the statute id and any
    further segments are section / subsection / item.
    """
    if not isinstance(serialized, str) or not serialized:
        raise ValueError(
            "graph_to_reference_mentions: reference_expr node missing "
            f"source_provision_ref (got {serialized!r})"
        )
    parts = serialized.split("/")
    # statute_id is the leading "A/B" pair (e.g. "123/2020"); the rest is the
    # in-act tail. A bare statute id (no tail) is the common case.
    statute_id = "/".join(parts[:2])
    tail = parts[2:]
    section_label = tail[0] if len(tail) > 0 else ""
    subsection_num = int(tail[1]) if len(tail) > 1 else None
    item_label = tail[2] if len(tail) > 2 else None
    return ProvisionRef(
        statute_id=statute_id,
        provision_path="",
        section_label=section_label,
        subsection_num=subsection_num,
        item_label=item_label,
    )


def _source_span_from_payload(payload: Mapping[str, object]) -> Optional[SourceSpan]:
    """Rebuild the authoritative byte :class:`SourceSpan` from the node payload.

    The lens stashes the byte-origin span as three flat payload keys
    (``source_span_file`` / ``source_span_byte_offset`` / ``source_span_len``),
    kept DISTINCT from the graph's char-coordinate ``source_ref``. None on all
    three == typed-absent span (metadata-derived mention). A partial trio is a
    corruption (fail loud).
    """
    file_ = payload.get("source_span_file")
    offset = payload.get("source_span_byte_offset")
    length = payload.get("source_span_len")
    if file_ is None and offset is None and length is None:
        return None
    if not (isinstance(file_, str) and isinstance(offset, int) and isinstance(length, int)):
        raise ValueError(
            "graph_to_reference_mentions: partial/typed-broken source byte span "
            f"(file={file_!r}, offset={offset!r}, len={length!r})"
        )
    return SourceSpan(source_file=file_, byte_offset=offset, byte_len=length)


def _valid_interval_from_payload(
    payload: Mapping[str, object],
) -> tuple[Optional[date], Optional[date]]:
    """Rebuild ``valid_at_interval`` from the ISO-string payload fields."""

    def _parse(value: object) -> Optional[date]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(
                f"graph_to_reference_mentions: valid_at field must be an ISO str, got {value!r}"
            )
        return date.fromisoformat(value)

    return _parse(payload.get("valid_at_start")), _parse(payload.get("valid_at_end"))


def graph_to_reference_mentions(
    graph: LegalSurfaceGraph,
) -> list[ReferenceMention]:
    """Reverse the ``ReferenceLens`` adapter back to ``ReferenceMention`` records.

    Walks every ``reference_expr`` node, joins its ``reference_resolution`` via
    the ``resolution_of`` edge, and reconstructs a :class:`ReferenceMention`
    equivalent to what ``extract_all_reference_mentions`` produced — for the
    fields the node payload carries.

    FULL-ROW FIDELITY (Phase 3b): ``source_span`` (authoritative byte origin),
    ``valid_at_interval``, ``target_stat_hash``, and the full source provision
    ref (incl. ``section_label``) are reconstructed from the enriched payload —
    not fabricated, and never confused with the graph's char-coordinate
    ``source_ref``. All 14 fi_refs fields round-trip exactly.

    Each ``reference_expr`` is joined to its ``reference_resolution`` via the
    intrinsic ``resolution_of`` edge (resolution -> expr) the lens always mints;
    the join is validated (every expr has exactly one resolution whose
    ``cite_confidence`` agrees) so a structurally broken graph fails loud rather
    than projecting a half-record. The fi_refs row carries no resolution-outcome
    column, so the resolution payload contributes no row field — but the join
    being present is part of the parity contract.
    """
    by_expr = _resolution_index(graph)
    expr_nodes = [
        node for node in graph.nodes.values() if node.node_kind == "reference_expr"
    ]

    mentions: list[ReferenceMention] = []
    for expr in expr_nodes:
        payload = expr.payload
        source_ref = expr.source_ref

        # Validate the intrinsic resolution_of join (fail loud on a broken graph).
        resolution = by_expr.get(expr.node_id)
        if resolution is None:
            raise ValueError(
                "graph_to_reference_mentions: reference_expr node has no "
                f"resolution_of -> reference_resolution edge (node_id={expr.node_id!r})"
            )
        if resolution.payload.get("cite_confidence") != payload.get("cite_confidence"):
            raise ValueError(
                "graph_to_reference_mentions: reference_resolution cite_confidence "
                f"{resolution.payload.get('cite_confidence')!r} disagrees with "
                f"reference_expr {payload.get('cite_confidence')!r} "
                f"(node_id={expr.node_id!r})"
            )

        cite_kind = _cite_kind_from_value(payload.get("cite_kind"))
        cite_confidence = _cite_confidence_from_value(payload.get("cite_confidence"))
        phrase_lemma = payload.get("phrase_lemma")
        if not isinstance(phrase_lemma, str) or not phrase_lemma:
            raise ValueError(
                "graph_to_reference_mentions: reference_expr node missing "
                f"phrase_lemma (node_id={expr.node_id!r})"
            )
        edge_subtype_value = payload.get("edge_subtype")
        edge_subtype = (
            edge_subtype_value if isinstance(edge_subtype_value, str) else None
        )

        # Source provision ref (gap #4): the full serialized citing provision
        # (statute id + section label) rides the payload, so the section is no
        # longer lost. source_ref.work_id is a cross-check on the statute id.
        source_provision_ref = _source_ref_from_payload(
            payload.get("source_provision_ref")
        )
        if source_ref is not None and source_ref.work_id is not None:
            if source_provision_ref.statute_id != source_ref.work_id:
                raise ValueError(
                    "graph_to_reference_mentions: source_provision_ref statute "
                    f"{source_provision_ref.statute_id!r} disagrees with "
                    f"source_ref.work_id {source_ref.work_id!r} (node_id={expr.node_id!r})"
                )

        target_provision_ref = _target_ref_from_payload(
            payload.get("target_id"),
            payload.get("target_provision_ref"),
        )

        surface_value = payload.get("surface_text")
        surface_text = surface_value if isinstance(surface_value, str) else ""

        target_stat_hash_value = payload.get("target_stat_hash")
        target_stat_hash = (
            target_stat_hash_value
            if isinstance(target_stat_hash_value, str)
            else None
        )

        mention = ReferenceMention(
            source_provision_ref=source_provision_ref,
            target_provision_ref=target_provision_ref,
            cite_kind=cite_kind,
            cite_confidence=cite_confidence,
            phrase_lemma=phrase_lemma,
            # Gap #1: authoritative BYTE span, reconstructed from the payload's
            # byte-origin fields (DISTINCT from the graph's char-coord source_ref).
            source_span=_source_span_from_payload(payload),
            # Gap #2: validity interval from the ISO-string payload fields.
            valid_at_interval=_valid_interval_from_payload(payload),
            edge_subtype=edge_subtype,
            # Gap #3: target_stat_hash from the payload.
            target_stat_hash=target_stat_hash,
            surface_text=surface_text,
        )
        mentions.append(mention)

    return mentions


def graph_to_fi_refs_rows(graph: LegalSurfaceGraph) -> list[dict[str, object]]:
    """Project the graph to ``fi_refs`` rows via the SAME row function.

    Reconstructs ``ReferenceMention`` records from the graph
    (:func:`graph_to_reference_mentions`) and runs them through the canonical
    ``reference_mention_to_row`` projection — the identical function the current
    extractor path uses (``export_fi_refs._project_refs_for_statute``). With the
    Phase-3b payload enrichment every fi_refs field is reconstructed exactly.

    ORDERING: rows are returned in ``reference_expr`` node iteration order. The
    extractor emits a deterministic per-statute order (domestic, then EU, then
    plain-text, then surface-grammar). The two orders are NOT guaranteed to
    match, so the parity gate compares as an order-insensitive MULTISET keyed on
    :data:`ROUND_TRIPPABLE_ROW_FIELDS` (now the full 14-field schema).
    """
    return [reference_mention_to_row(m) for m in graph_to_reference_mentions(graph)]
