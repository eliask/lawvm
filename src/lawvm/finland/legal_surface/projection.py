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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Optional, cast

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
from lawvm.finland.legal_surface.lenses.references import (
    LENS_ID as _REFERENCES_LENS_ID,
)
from lawvm.finland.references.resolve import (
    SuccessorReferenceReasonCode,
    SuccessorReferenceResolutionBasis,
    SuccessorReferenceStatus,
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


@dataclass(frozen=True, slots=True)
class ReferenceSuccessorChainWitness:
    """One witnessed edge in a projected successor chain."""

    predecessor_work_id: str
    successor_work_id: str
    effective_from: date
    witness_id: str
    witness_text: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class ReferenceSuccessorProjectionRow:
    """Public projection row for B5 dated successor resolution.

    This is intentionally separate from the legacy ``fi_refs`` row. ``fi_refs``
    preserves the literal citation surface; successor resolution is a dated
    operative-endpoint claim attached to the joined ``reference_resolution``.
    """

    source_work_id: str
    source_provision_ref_str: str
    source_span_file: str | None
    source_span_byte_offset: int | None
    source_span_len: int | None
    surface_text: str
    literal_work_id: str | None
    operative_work_id: str | None
    successor_as_of: str | None
    successor_status: SuccessorReferenceStatus
    successor_resolution_basis: SuccessorReferenceResolutionBasis
    successor_candidates: tuple[str, ...]
    successor_rejected_candidates: tuple[str, ...]
    successor_reason_code: SuccessorReferenceReasonCode
    successor_chain: tuple[ReferenceSuccessorChainWitness, ...]


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


def _ref_from_serialized_tail(statute_id: str, tail: str) -> ProvisionRef:
    """Rebuild a :class:`ProvisionRef` from a TYPED serialized tail.

    ``tail`` is the ``/``-joined remainder after the statute id in
    :meth:`ProvisionRef.serialized` — the self-describing form
    ``[chN/]section[/momentti][/kLABEL]``. Each non-section segment is typed:

      * ``ch{N}`` — chapter (reconstructed into ``provision_path`` as the AKN
        ``chp_N`` head so ``_chapter_from_provision_path`` yields it back);
      * bare integer — momentti (subsection); the only bare non-section segment;
      * ``k{LABEL}`` — kohta (item).

    The reconstruction is round-trip-faithful: the returned ref's
    :meth:`~ProvisionRef.serialized` reproduces ``statute_id`` + ``/`` + ``tail``
    byte-identically (the parity contract). The chapter ``provision_path`` head
    is the only synthetic field — the section/momentti/kohta are not duplicated
    into ``provision_path`` because ``serialized`` reads only the chapter from it.
    """
    chapter: Optional[str] = None
    section_label = ""
    subsection_num: Optional[int] = None
    item_label: Optional[str] = None

    if tail:
        segments = tail.split("/")
        idx = 0
        if segments[idx].startswith("ch"):
            chapter = segments[idx][len("ch") :]
            idx += 1
        if idx < len(segments):
            section_label = segments[idx]
            idx += 1
        # After the section, a bare-integer segment is momentti; a ``k``-prefixed
        # segment is kohta. Either, both, or neither may be present.
        if idx < len(segments) and not segments[idx].startswith("k"):
            subsection_num = int(segments[idx])
            idx += 1
        if idx < len(segments) and segments[idx].startswith("k"):
            item_label = segments[idx][len("k") :]
            idx += 1
        if idx != len(segments):
            raise ValueError(
                "graph_to_reference_mentions: unparseable serialized provision "
                f"tail {tail!r} for statute {statute_id!r}"
            )

    provision_path = f"chp_{chapter}" if chapter is not None else ""
    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=section_label,
        subsection_num=subsection_num,
        item_label=item_label,
    )


def _target_ref_from_payload(
    target_id: object,
    serialized: object,
) -> Optional[ProvisionRef]:
    """Rebuild a target :class:`ProvisionRef` from the lens payload.

    The lens stored two fields per expr node:
      * ``target_id``             — the canonical statute id (e.g. ``1982/633``);
      * ``target_provision_ref``  — ``ProvisionRef.serialized()``
                                    (``statute_id[/chN]/section[/momentti][/kLABEL]``).

    The statute id ITSELF contains a ``/`` (``YEAR/NUMBER``), so the serialized
    string cannot be split on ``/`` blindly. We use ``target_id`` as the
    authoritative statute-id boundary, strip exactly that prefix off the
    serialized string, and parse the TYPED remaining tail (see
    :func:`_ref_from_serialized_tail`) so that ``serialized()`` reproduces the
    stored ``target_provision_ref`` string byte-identically — the
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

    return _ref_from_serialized_tail(target_id, tail)


def _source_ref_from_payload(serialized: object) -> ProvisionRef:
    """Rebuild the SOURCE :class:`ProvisionRef` from its serialized payload field.

    The lens stashes ``source_provision_ref`` = ``ProvisionRef.serialized()`` of
    the citing provision. We rebuild it so ``serialized()`` reproduces
    ``source_provision_ref_str`` byte-identically. The statute id itself contains
    a ``/`` (``YEAR/NUMBER`` or ``NUMBER/YEAR``), so the first TWO ``/``-segments
    form the statute id and the typed remainder is parsed by
    :func:`_ref_from_serialized_tail`.
    """
    if not isinstance(serialized, str) or not serialized:
        raise ValueError(
            "graph_to_reference_mentions: reference_expr node missing "
            f"source_provision_ref (got {serialized!r})"
        )
    parts = serialized.split("/")
    # statute_id is the leading "A/B" pair (e.g. "123/2020"); the rest is the
    # typed in-act tail.
    statute_id = "/".join(parts[:2])
    tail = "/".join(parts[2:])
    return _ref_from_serialized_tail(statute_id, tail)


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


def _optional_str_payload(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"graph_to_reference_successor_rows: {key} must be a str or None, "
            f"got {value!r}"
        )
    return value


def _required_str_payload(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"graph_to_reference_successor_rows: {key} must be a non-empty str, "
            f"got {value!r}"
        )
    return value


def _tuple_str_payload(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(
            f"graph_to_reference_successor_rows: {key} must be a list[str], "
            f"got {value!r}"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                f"graph_to_reference_successor_rows: {key} contains non-str "
                f"value {item!r}"
            )
        out.append(item)
    return tuple(out)


def _required_date_payload(payload: Mapping[str, object], key: str) -> date:
    raw = _required_str_payload(payload, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"graph_to_reference_successor_rows: {key} must be an ISO date, "
            f"got {raw!r}"
        ) from exc


def _successor_chain_payload(
    payload: Mapping[str, object],
) -> tuple[ReferenceSuccessorChainWitness, ...]:
    value = payload.get("successor_chain")
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(
            "graph_to_reference_successor_rows: successor_chain must be a "
            f"list[dict], got {value!r}"
        )
    chain: list[ReferenceSuccessorChainWitness] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(
                "graph_to_reference_successor_rows: successor_chain contains "
                f"non-mapping value {item!r}"
            )
        item_map = cast(Mapping[str, object], item)
        chain.append(
            ReferenceSuccessorChainWitness(
                predecessor_work_id=_required_str_payload(
                    item_map, "predecessor_work_id"
                ),
                successor_work_id=_required_str_payload(
                    item_map, "successor_work_id"
                ),
                effective_from=_required_date_payload(item_map, "effective_from"),
                witness_id=_required_str_payload(item_map, "witness_id"),
                witness_text=_required_str_payload(item_map, "witness_text"),
                rule_id=_required_str_payload(item_map, "rule_id"),
            )
        )
    return tuple(chain)


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
    # Only the H1 ReferenceLens (``fi.references.v0``) mints the fi_refs-bearing
    # ``reference_expr`` nodes this projection inverts. Other lenses (notably the
    # discourse AnaphoraLens, ``fi.anaphora.v0``) REUSE the ``reference_expr`` node
    # kind for a uniform census, but their payload carries a discourse
    # ``resolution_status`` rather than the fi_refs ``cite_confidence`` — they are
    # NOT fi_refs rows. Scope the projection to the references lens so those census
    # nodes are not mis-read as fi_refs mentions (which would fail-loud on the
    # absent ``cite_confidence``). The fail-loud check on the references-lens nodes
    # is unchanged: a ``fi.references.v0`` expr without a valid cite_confidence
    # still raises.
    expr_nodes = [
        node
        for node in graph.nodes.values()
        if node.node_kind == "reference_expr" and node.lens_id == _REFERENCES_LENS_ID
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


def graph_to_reference_successor_rows(
    graph: LegalSurfaceGraph,
) -> list[ReferenceSuccessorProjectionRow]:
    """Project B5 successor-resolution payloads to public rows.

    The ordinary ``fi_refs`` projection stays literal-citation-only. This
    function exposes the separate dated successor layer carried by H1
    ``reference_resolution`` payloads. It emits one row per H1 ``reference_expr``
    whose joined resolution carries ``successor_resolution_status``; graphs built
    without successor context return an empty list.
    """
    by_expr = _resolution_index(graph)
    expr_nodes = [
        node
        for node in graph.nodes.values()
        if node.node_kind == "reference_expr" and node.lens_id == _REFERENCES_LENS_ID
    ]

    rows: list[ReferenceSuccessorProjectionRow] = []
    for expr in expr_nodes:
        resolution = by_expr.get(expr.node_id)
        if resolution is None:
            raise ValueError(
                "graph_to_reference_successor_rows: reference_expr node has no "
                f"resolution_of -> reference_resolution edge (node_id={expr.node_id!r})"
            )
        payload = resolution.payload
        if "successor_resolution_status" not in payload:
            continue
        source_ref = expr.source_ref
        if source_ref is None or not source_ref.work_id:
            raise ValueError(
                "graph_to_reference_successor_rows: successor row needs a "
                f"source_ref with work_id (node_id={expr.node_id!r})"
            )
        source_provision_ref = _source_ref_from_payload(
            expr.payload.get("source_provision_ref")
        )
        source_span = _source_span_from_payload(expr.payload)
        rows.append(
            ReferenceSuccessorProjectionRow(
                source_work_id=source_ref.work_id,
                source_provision_ref_str=source_provision_ref.serialized(),
                source_span_file=(
                    source_span.source_file if source_span is not None else None
                ),
                source_span_byte_offset=(
                    source_span.byte_offset if source_span is not None else None
                ),
                source_span_len=source_span.byte_len if source_span is not None else None,
                surface_text=_optional_str_payload(expr.payload, "surface_text") or "",
                literal_work_id=_optional_str_payload(payload, "literal_work_id"),
                operative_work_id=_optional_str_payload(payload, "operative_work_id"),
                successor_as_of=_optional_str_payload(payload, "successor_as_of"),
                successor_status=SuccessorReferenceStatus(
                    _required_str_payload(payload, "successor_resolution_status")
                ),
                successor_resolution_basis=SuccessorReferenceResolutionBasis(
                    _required_str_payload(payload, "successor_resolution_basis")
                ),
                successor_candidates=_tuple_str_payload(
                    payload, "successor_candidates"
                ),
                successor_rejected_candidates=_tuple_str_payload(
                    payload, "successor_rejected_candidates"
                ),
                successor_reason_code=SuccessorReferenceReasonCode(
                    _required_str_payload(payload, "successor_reason_code")
                ),
                successor_chain=_successor_chain_payload(payload),
            )
        )
    return rows
