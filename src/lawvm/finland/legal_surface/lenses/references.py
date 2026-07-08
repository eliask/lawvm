"""H1 REFERENCES lens — adapter from ReferenceMention output to graph seeds.

Pro r5 Phase 2, §D3 ("Stage 1 adapter mode"), §D5 ("ambiguous/open endpoints").

This lens is the Stage-1 bridge: it runs the existing Finnish reference
recognizers (``extract_all_reference_mentions``) over the bundle's per-unit
``xml_bytes`` and turns each :class:`ReferenceMention` into Legal Surface Graph
seeds the core assembler mints. It does NOT fetch source itself and does NOT edit
the recognizers or the core — it is a pure adapter (§D4 substrate rule).

The two-stage model (§D3/§D5) is encoded structurally per mention:

  * a ``reference_expr`` node      — what the text SAYS (the citation surface);
  * a ``reference_resolution`` node — what it POINTS TO (the resolution outcome);
  * a ``resolution_of`` edge        — resolution -> expr (intrinsic).

Resolution endpoints (only when registries are supplied via ``context.options``;
otherwise this lens emits expr+resolution+resolution_of only and resolves no
targets):

  * resolved / unchanged -> a ``legal_work_entity`` node + a ``refers_to`` edge
    (resolution -> entity), status ``asserted``. The assertion is made ONLY when
    a single unambiguous target exists (§D5).
  * ambiguous            -> a ``has_candidate`` edge (status ``candidate``) to
    EACH candidate ``legal_work_entity``; NO ``refers_to`` is asserted (§D5).
  * open / statute_only / broken / unsupported -> an ``unresolved_because`` edge
    (status mirrors the outcome) to a ``surface_residual`` node carrying the
    reason; the target is never guessed (§D5, fail-loud).

Every rejected candidate and every unlocatable surface becomes an explicit
:class:`SurfaceResidualSeed` (fail-loud; never a silent drop).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from typing import Optional, cast

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
    source_bytes_of,
)
from lawvm.core.reference_mention import (
    CiteConfidence,
    ReferenceMention,
    RejectedRefCandidate,
)
from lawvm.finland.legal_surface.bundle import decode_body_text, locate_span
from lawvm.finland.references.defined_terms import (
    DefinedTermBinding,
    recognize_defined_term_bindings,
)
from lawvm.finland.references.elliptical_resolve import resolve_elliptical_mentions
from lawvm.finland.references.ref_mention_extractor import (
    ExtractionResult,
    extract_all_reference_mentions,
)
from lawvm.finland.references.registries.statute_name import StatuteNameRegistry
from lawvm.finland.references.resolve import (
    DefinedTermTable,
    ResolutionStatus,
    ResolvedReference,
    StatuteSuccessorEdge,
    SuccessorReferenceResolution,
    build_defined_term_table,
    resolve_mentions,
    resolve_successor_reference,
)

LENS_ID = "fi.references.v0"
SCHEMA_VERSION = "v0"

# Rule ids (stable; the witness/identity carrier for each emitted seed family).
_RULE_EXPR = "fi.references.v0.reference_expr"
_RULE_RESOLUTION = "fi.references.v0.reference_resolution"
_RULE_RESOLUTION_OF = "fi.references.v0.resolution_of"
_RULE_REFERS_TO = "fi.references.v0.refers_to"
_RULE_HAS_CANDIDATE = "fi.references.v0.has_candidate"
_RULE_UNRESOLVED_BECAUSE = "fi.references.v0.unresolved_because"
_RULE_REJECTED = "fi.references.v0.rejected_candidate"
_RULE_UNLOCATABLE = "fi.references.v0.unlocatable_surface"


# ── cite_confidence -> graph ResolutionStatus (NODE_STATUSES) ────────────────
#
# The graph's status vocabulary (``legal_surface_graph.ResolutionStatus``) is the
# string literal set {resolved, statute_only, ambiguous, open, broken,
# unsupported}. We map each CiteConfidence onto it:
#
#   EXACT       -> resolved      (target identity is fixed)
#   APPROXIMATE -> resolved      (a heuristic resolution is still a resolution;
#                                 the recogniser already committed a target)
#   STATUTE_ONLY-> statute_only  (act identity textual, provision/id pending)
#   AMBIGUOUS   -> ambiguous     (>1 plausible target; never picked here)
#   OPEN        -> open          (vague catch-all; names no target by design)
#   BROKEN      -> broken        (target repealed/renumbered after the cite)
#   UNRESOLVED  -> unsupported   (no resolvable target — unsupported by identity)
_CONFIDENCE_TO_STATUS: dict[CiteConfidence, str] = {
    CiteConfidence.EXACT: "resolved",
    CiteConfidence.APPROXIMATE: "resolved",
    CiteConfidence.STATUTE_ONLY: "statute_only",
    CiteConfidence.AMBIGUOUS: "ambiguous",
    CiteConfidence.OPEN: "open",
    CiteConfidence.BROKEN: "broken",
    CiteConfidence.UNRESOLVED: "unsupported",
}


def _status_for_confidence(confidence: CiteConfidence) -> str:
    """Map a ReferenceMention's cite_confidence to a graph node status.

    Fail-loud: an unmapped confidence raises rather than silently defaulting.
    """
    try:
        return _CONFIDENCE_TO_STATUS[confidence]
    except KeyError as exc:  # pragma: no cover — closed enum, defensive
        raise ValueError(
            f"{LENS_ID}: no graph status mapping for cite_confidence {confidence!r}"
        ) from exc


def _parse_iso_date(value: str | None) -> Optional[dt.date]:
    """Parse an ISO ``YYYY-MM-DD`` (date or datetime) bound to a ``date``.

    Returns ``None`` for an empty / unparseable bound — an unknown bound stays
    open (fail-loud: an unparseable date is NOT silently coerced to a guessed
    instant, it simply leaves the interval open on that side).
    """
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _successor_edges_option(value: object) -> tuple[StatuteSuccessorEdge, ...]:
    """Read typed successor edges from the lens context.

    Context options are intentionally loose at the lens boundary, so this helper
    validates the only shape B5 consumes. A malformed option fails loud instead
    of being ignored as "no successors".
    """
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)):
        raise TypeError(
            f"{LENS_ID}: successor_edges option must be a sequence of "
            f"StatuteSuccessorEdge, got {type(value).__name__}"
        )
    edges: list[StatuteSuccessorEdge] = []
    for edge in value:
        if not isinstance(edge, StatuteSuccessorEdge):
            raise TypeError(
                f"{LENS_ID}: successor_edges option contains non-"
                f"StatuteSuccessorEdge value {edge!r}"
            )
        edges.append(edge)
    return tuple(edges)


def _successor_as_of_option(value: object) -> Optional[dt.date]:
    """Read the optional dated successor-resolution instant from context."""
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value[:10])
    raise TypeError(
        f"{LENS_ID}: successor_as_of option must be a date or ISO date string, "
        f"got {type(value).__name__}"
    )


def _unit_validity_interval(
    unit: SourceSurfaceUnit,
) -> tuple[Optional[dt.date], Optional[dt.date]]:
    """The validity window of this unit's consolidated text as a date interval.

    Sourced from :attr:`SourceSurfaceUnit.effective_interval` (ISO strings). This
    is the interval during which THIS version of the body text was in force, and
    is threaded onto each emitted mention's ``valid_at_interval`` so a by-name
    citation resolves to the version of the cited act in force WHILE the citing
    text was valid (static-as-of-citing at the unit's granularity). An open start
    (``None``) keeps a multi-version name AMBIGUOUS downstream (no guess).
    """
    start_raw, end_raw = unit.effective_interval
    return (_parse_iso_date(start_raw), _parse_iso_date(end_raw))


def _normalize_surface(surface_text: str) -> str:
    """Whitespace-collapsed surface for a stable local discriminator."""
    return " ".join(surface_text.split())


def _unlocatable_text_hash(mention: ReferenceMention, index: int) -> str:
    """Content address for a mention whose surface cannot be char-anchored.

    Keyed off the mention's surface (if any), its phrase class, and the running
    index so two distinct unlocatable mentions never collide on text_hash.
    """
    seed = f"{LENS_ID}::unlocatable#{index}|{mention.phrase_lemma}|{mention.surface_text}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _expr_local(index: int) -> str:
    return f"{LENS_ID}::expr#{index}"


def _resolution_local(index: int) -> str:
    return f"{LENS_ID}::resolution#{index}"


def _entity_local(work_id: str) -> str:
    # Entity nodes are minted as ``entity:<discriminator>`` by the assembler; the
    # discriminator IS the canonical work id.
    return work_id


class ReferenceLens:
    """The H1 REFERENCES surface lens (satisfies the ``SurfaceLens`` protocol)."""

    lens_id: str = LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = SCHEMA_VERSION
    produces_node_kinds: tuple[str, ...] = (
        "reference_expr",
        "reference_resolution",
        "legal_work_entity",
        "surface_residual",
    )
    produces_edge_kinds: tuple[str, ...] = (
        "resolution_of",
        "refers_to",
        "has_candidate",
        "unresolved_because",
    )
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        edge_seeds: list[SurfaceEdgeSeed] = []
        residuals: list[SurfaceResidualSeed] = []

        statute_registry = context.options.get("statute_registry")
        eu_registry = context.options.get("eu_registry")
        resolve_targets = statute_registry is not None
        successor_edges = _successor_edges_option(context.options.get("successor_edges"))
        successor_as_of = _successor_as_of_option(context.options.get("successor_as_of"))

        n_units = 0
        n_mentions = 0
        n_resolved = 0
        n_ambiguous = 0
        n_unresolved = 0

        # Global running index so reference_expr / reference_resolution
        # discriminators are unique across all units in the bundle.
        index = 0

        for unit in bundle.units:
            n_units += 1
            # Read the raw AKN XML from the TYPED unit view (§D4 bridge), not a
            # free-form metadata key. Absence is fail-loud: a typed residual.
            xml_bytes = source_bytes_of(unit)
            if not isinstance(xml_bytes, (bytes, bytearray)):
                residuals.append(
                    SurfaceResidualSeed(
                        residual_kind="missing_xml_bytes",
                        source_ref=unit.source_ref,
                        local_discriminator=f"{LENS_ID}::missing_xml::{unit.source_unit_id}",
                        rule_id=_RULE_UNLOCATABLE,
                        reason_code="unit_has_no_source_bytes",
                        payload={"source_unit_id": unit.source_unit_id},
                        residual_status="blocked",
                    )
                )
                continue

            # The validity window of this consolidated body version, threaded
            # onto every mention's valid_at_interval so a multi-version by-name
            # citation resolves to the act version in force WHILE this text held
            # (rather than collapsing to AMBIGUOUS over the whole timeline). An
            # open window leaves the mention's interval open → AMBIGUOUS (no
            # guess); see resolve_mentions(use_mention_validity=True) below.
            unit_interval = _unit_validity_interval(unit)
            extraction: ExtractionResult = extract_all_reference_mentions(
                bytes(xml_bytes), unit.work_id, valid_at_interval=unit_interval
            )

            # Elliptical INTERNAL refs (bare momentti / bare kohta) omit part of
            # their address; the recognizer leaves it empty, which would resolve
            # to the whole-statute root. Fill the omitted section (convention) /
            # momentti (structural uniqueness) against the materialized AKN tree
            # BEFORE the rest of the pipeline reads each mention's target. The
            # pass is order- and cardinality-preserving (one resolution per input
            # mention) and downgrades to ambiguous/open fail-loud, never to root.
            mentions = [
                res.mention
                for res in resolve_elliptical_mentions(
                    list(extraction.mentions), bytes(xml_bytes)
                )
            ]

            resolutions: list[ResolvedReference | None] = []
            if resolve_targets:
                # Per-statute local defined-term / alias table. The recognizer
                # reports binding spans as CHAR offsets into the decoded body
                # text; the use mentions are re-anchored to BYTE offsets in
                # ``xml_bytes`` (ref_mention_extractor._relocate). The table's
                # ordering check ("binding precedes use") compares offsets, so
                # the binding sites MUST live in the same byte space. We
                # therefore re-anchor each binding onto its term TOKEN — which
                # byte-matches ``xml_bytes`` verbatim — before building the table.
                defined_terms = _build_local_defined_term_table(
                    bytes(xml_bytes), unit.work_id
                )
                resolutions.extend(
                    resolve_mentions(
                        mentions,
                        statute_registry=cast(StatuteNameRegistry, statute_registry),
                        eu_registry=eu_registry if eu_registry is not None else _default_eu_registry(),
                        defined_terms=defined_terms,
                        # Resolve each by-name mention against the version of the
                        # cited act in force WHILE that mention's reference state
                        # held (its valid_at_interval start), rather than collapsing
                        # every multi-version name to AMBIGUOUS. An open/unknown
                        # interval keeps it AMBIGUOUS (no guess); the citing
                        # statute's enactment year is never used.
                        use_mention_validity=True,
                    )
                )
            else:
                resolutions.extend(None for _ in mentions)

            cursor = 0
            for mention, resolved in zip(mentions, resolutions, strict=True):
                n_mentions += 1
                located_ref, cursor = self._locate(unit, mention, cursor)
                # Every real mention becomes a reference_expr node — no locatable
                # mention is dropped to a residual (the Phase-3b cardinality gap
                # closure). When the surface cannot be char-anchored in raw_text
                # (empty/normalized surface, or a metadata edge with no body
                # surface), we still mint the node against a byte-span-derived /
                # index-based fallback source_ref so the full mention set round-
                # trips to fi_refs rows. The authoritative byte origin always
                # rides the payload, independent of this graph char coordinate.
                source_ref = located_ref or self._fallback_source_ref(
                    unit, mention, index
                )

                expr_local = _expr_local(index)
                resolution_local = _resolution_local(index)
                node_status = _status_for_confidence(mention.cite_confidence)

                node_seeds.append(
                    self._reference_expr_seed(
                        mention, source_ref=source_ref, resolution_status=node_status,
                        local=expr_local,
                    )
                )
                node_seeds.append(
                    self._reference_resolution_seed(
                        mention,
                        resolved=resolved,
                        successor_resolution=(
                            resolve_successor_reference(
                                resolved,
                                as_of=successor_as_of,
                                successor_edges=successor_edges,
                            )
                            if resolved is not None and successor_edges
                            else None
                        ),
                        source_ref=source_ref,
                        resolution_status=node_status,
                        local=resolution_local,
                    )
                )
                edge_seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="resolution_of",
                        src_local=resolution_local,
                        dst_local=expr_local,
                        rule_id=_RULE_RESOLUTION_OF,
                        surface_edge_status="asserted",
                        payload={},
                    )
                )

                if resolved is not None:
                    counted = self._emit_resolution_endpoints(
                        resolved,
                        resolution_local=resolution_local,
                        index=index,
                        source_ref=source_ref,
                        node_seeds=node_seeds,
                        edge_seeds=edge_seeds,
                        residuals=residuals,
                    )
                    n_resolved += counted.resolved
                    n_ambiguous += counted.ambiguous
                    n_unresolved += counted.unresolved

                index += 1

            # Rejected candidates are first-class residuals (§1.8, fail-loud).
            for rej in extraction.rejected:
                residuals.append(self._rejected_residual(rej, unit, index))
                index += 1

        coverage: dict[str, object] = {
            "units": n_units,
            "mentions": n_mentions,
            "resolved": n_resolved,
            "ambiguous": n_ambiguous,
            "unresolved": n_unresolved,
            "residuals": len(residuals),
            "resolution_enabled": resolve_targets,
        }

        return SurfaceLensResult(
            lens_id=LENS_ID,
            node_seeds=tuple(node_seeds),
            edge_seeds=tuple(edge_seeds),
            residuals=tuple(residuals),
            diagnostics=(),
            coverage=coverage,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _locate(
        unit: SourceSurfaceUnit,
        mention: ReferenceMention,
        cursor: int,
    ) -> tuple[SourceSpanRef | None, int]:
        """Anchor a mention's surface in ``raw_text`` via ``locate_span``.

        Advances a left-to-right cursor so repeated identical surfaces map to
        successive occurrences. Because two mentions can name the SAME textual
        occurrence (e.g. the ``<ref>`` lane and the by-name lane both matching
        one ``lannoitelaissa``), a cursor miss retries from 0 before declaring
        the surface unlocatable — fail-loud only when the text is genuinely
        absent, never a fabricated offset.
        """
        surface = mention.surface_text or ""
        if not surface:
            return None, cursor
        ref, next_cursor = locate_span(unit, surface, cursor=cursor)
        if ref is not None:
            return ref, next_cursor
        # Retry from the start: a shared occurrence already consumed by the cursor.
        ref, _ = locate_span(unit, surface, cursor=0)
        return ref, cursor

    @staticmethod
    def _fallback_source_ref(
        unit: SourceSurfaceUnit,
        mention: ReferenceMention,
        index: int,
    ) -> SourceSpanRef:
        """A graph char-anchor for a mention whose surface can't be char-located.

        Source-fact nodes require a ``source_ref`` (the graph's CHAR coordinate
        into ``raw_text``). When a mention's surface is empty or normalized away
        (the plain_text/metadata/EU lanes), or simply absent from ``raw_text``,
        ``locate_span`` returns None — yet the mention is real and must still
        mint a ``reference_expr`` node (no silent drop to residual).

        We therefore synthesize a DEGENERATE char span. It is explicitly NOT a
        recovered offset: char_start == char_end (zero-length), anchored at the
        unit origin. The authoritative byte span lives in the node payload
        (``source_span_*``), so the lossy char fallback never contaminates the
        round-tripped fi_refs row. ``text_hash`` keys off the index so distinct
        unlocatable mentions get distinct content addresses (defensive; the
        ``expr#{index}`` discriminator already makes node ids unique).
        """
        return SourceSpanRef(
            source_unit_id=unit.source_unit_id,
            source_hash=unit.source_hash,
            work_id=unit.work_id,
            address=unit.address,
            char_start=0,
            char_end=0,
            text_hash=_unlocatable_text_hash(mention, index),
        )

    @staticmethod
    def _reference_expr_seed(
        mention: ReferenceMention,
        *,
        source_ref: SourceSpanRef,
        resolution_status: str,
        local: str,
    ) -> SurfaceNodeSeed:
        target = mention.target_provision_ref
        # AUTHORITATIVE byte-origin span (into xml_bytes), stashed verbatim so the
        # row projection reproduces the extractor's fi_refs span byte-identically.
        # This is DISTINCT from ``source_ref`` (a CHAR anchor into raw_text — the
        # graph's own coordinate). Char-coord stays the graph coordinate; byte
        # origin rides the payload so neither coordinate space is mangled into the
        # other. None for metadata-derived mentions with no body surface.
        byte_span = mention.source_span
        valid_start, valid_end = mention.valid_at_interval
        payload: dict[str, object] = {
            "surface_text": mention.surface_text,
            "cite_kind": mention.cite_kind.value,
            "cite_confidence": mention.cite_confidence.value,
            "phrase_lemma": mention.phrase_lemma,
            "edge_subtype": mention.edge_subtype,
            "target_id": target.statute_id if target is not None else None,
            "target_provision_ref": target.serialized() if target is not None else None,
            # Authoritative byte span (gap #1) — kept separate from the char-coord
            # ``source_ref``; reproduces source_span_{file,byte_offset,len}.
            "source_span_file": byte_span.source_file if byte_span is not None else None,
            "source_span_byte_offset": (
                byte_span.byte_offset if byte_span is not None else None
            ),
            "source_span_len": byte_span.byte_len if byte_span is not None else None,
            # Validity interval (gap #2) — ISO strings, the row's stored form.
            "valid_at_start": valid_start.isoformat() if valid_start is not None else None,
            "valid_at_end": valid_end.isoformat() if valid_end is not None else None,
            # Target stat hash (gap #3).
            "target_stat_hash": mention.target_stat_hash,
            # Source provision ref incl. citing section (gap #4) — serialized so
            # the full source_provision_ref_str round-trips, not just the work_id.
            "source_provision_ref": mention.source_provision_ref.serialized(),
        }
        discriminator = (
            f"{_normalize_surface(mention.surface_text)}|{mention.cite_kind.value}"
        )
        payload["local_key"] = discriminator
        return SurfaceNodeSeed(
            node_kind="reference_expr",
            source_ref=source_ref,
            local_discriminator=local,
            rule_id=_RULE_EXPR,
            node_status=resolution_status,
            payload=payload,
            authority_role="surface_fact",
        )

    @staticmethod
    def _reference_resolution_seed(
        mention: ReferenceMention,
        *,
        resolved: ResolvedReference | None,
        successor_resolution: SuccessorReferenceResolution | None,
        source_ref: SourceSpanRef,
        resolution_status: str,
        local: str,
    ) -> SurfaceNodeSeed:
        payload: dict[str, object] = {
            "cite_confidence": mention.cite_confidence.value,
            "surface_text": mention.surface_text,
        }
        if resolved is not None:
            payload["resolution_status"] = resolved.resolution_status.value
            payload["work_id"] = resolved.work_id
            payload["candidates"] = list(resolved.candidates)
        if successor_resolution is not None:
            payload.update(
                {
                    "successor_resolution_status": (
                        successor_resolution.successor_status.value
                    ),
                    "literal_work_id": successor_resolution.literal_work_id,
                    "operative_work_id": successor_resolution.operative_work_id,
                    "successor_as_of": (
                        successor_resolution.as_of.isoformat()
                        if successor_resolution.as_of is not None
                        else None
                    ),
                    "successor_resolution_basis": (
                        successor_resolution.resolution_basis.value
                    ),
                    "successor_candidates": list(successor_resolution.candidates),
                    "successor_rejected_candidates": list(
                        successor_resolution.rejected_candidates
                    ),
                    "successor_reason_code": successor_resolution.reason_code.value,
                    "successor_chain": [
                        {
                            "predecessor_work_id": edge.predecessor_work_id,
                            "successor_work_id": edge.successor_work_id,
                            "effective_from": edge.effective_from.isoformat(),
                            "witness_id": edge.witness_id,
                            "witness_text": edge.witness_text,
                            "rule_id": edge.rule_id,
                        }
                        for edge in successor_resolution.successor_chain
                    ],
                }
            )
        return SurfaceNodeSeed(
            node_kind="reference_resolution",
            source_ref=source_ref,
            local_discriminator=local,
            rule_id=_RULE_RESOLUTION,
            node_status=resolution_status,
            payload=payload,
            authority_role="surface_fact",
        )

    def _emit_resolution_endpoints(
        self,
        resolved: ResolvedReference,
        *,
        resolution_local: str,
        index: int,
        source_ref: SourceSpanRef,
        node_seeds: list[SurfaceNodeSeed],
        edge_seeds: list[SurfaceEdgeSeed],
        residuals: list[SurfaceResidualSeed],
    ) -> _EndpointCounts:
        """Emit the resolution's endpoint per §D5 (resolved/ambiguous/open)."""
        status = resolved.resolution_status

        if status in (ResolutionStatus.RESOLVED, ResolutionStatus.UNCHANGED):
            work_id = resolved.work_id
            if work_id:
                node_seeds.append(self._entity_seed(work_id))
                edge_seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="refers_to",
                        src_local=resolution_local,
                        dst_local=_entity_local(work_id),
                        rule_id=_RULE_REFERS_TO,
                        surface_edge_status="asserted",
                        payload={"work_id": work_id},
                    )
                )
                return _EndpointCounts(resolved=1)
            # Resolved-by-status but no concrete id: degrade to a residual.
            residuals.append(
                self._open_residual(
                    resolution_local, index, reason_code="resolved_without_work_id",
                    edge_status="blocked", edge_seeds=edge_seeds, residual_status="blocked",
                    source_ref=source_ref,
                )
            )
            return _EndpointCounts(unresolved=1)

        if status is ResolutionStatus.AMBIGUOUS:
            # has_candidate edges only; NO refers_to is asserted (§D5).
            for candidate in resolved.candidates:
                node_seeds.append(self._entity_seed(candidate))
                edge_seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="has_candidate",
                        src_local=resolution_local,
                        dst_local=_entity_local(candidate),
                        rule_id=_RULE_HAS_CANDIDATE,
                        surface_edge_status="candidate",
                        payload={"candidate_id": candidate},
                    )
                )
            return _EndpointCounts(ambiguous=1)

        # OPEN / STATUTE_ONLY / BROKEN (and any other) -> unresolved_because to a
        # residual (§D5). The target is never guessed.
        reason_code = {
            ResolutionStatus.OPEN: "vague_catch_all_names_no_target",
            ResolutionStatus.STATUTE_ONLY: "act_identity_textual_id_pending",
            ResolutionStatus.BROKEN: "target_repealed_or_renumbered",
        }.get(status, f"unresolved_{status.value}")
        edge_status = "blocked" if status is ResolutionStatus.BROKEN else "open"
        residual_status = "blocked" if status is ResolutionStatus.BROKEN else "open"
        residuals.append(
            self._open_residual(
                resolution_local,
                index,
                reason_code=reason_code,
                edge_status=edge_status,
                edge_seeds=edge_seeds,
                residual_status=residual_status,
                source_ref=source_ref,
            )
        )
        return _EndpointCounts(unresolved=1)

    @staticmethod
    def _entity_seed(work_id: str) -> SurfaceNodeSeed:
        return SurfaceNodeSeed(
            node_kind="legal_work_entity",
            source_ref=None,
            local_discriminator=_entity_local(work_id),
            rule_id=_RULE_REFERS_TO,
            node_status="present",
            payload={"work_id": work_id},
            authority_role="entity_handle",
        )

    @staticmethod
    def _open_residual(
        resolution_local: str,
        index: int,
        *,
        reason_code: str,
        edge_status: str,
        edge_seeds: list[SurfaceEdgeSeed],
        residual_status: str,
        source_ref: SourceSpanRef,
    ) -> SurfaceResidualSeed:
        residual_local = f"{LENS_ID}::residual#{index}"
        edge_seeds.append(
            SurfaceEdgeSeed(
                edge_kind="unresolved_because",
                src_local=resolution_local,
                dst_local=residual_local,
                rule_id=_RULE_UNRESOLVED_BECAUSE,
                surface_edge_status=edge_status,
                payload={"reason_code": reason_code},
            )
        )
        return SurfaceResidualSeed(
            residual_kind="unresolved_reference",
            source_ref=source_ref,
            local_discriminator=residual_local,
            rule_id=_RULE_UNRESOLVED_BECAUSE,
            reason_code=reason_code,
            payload={"reason_code": reason_code},
            residual_status=residual_status,
        )

    @staticmethod
    def _rejected_residual(
        rej: RejectedRefCandidate,
        unit: SourceSurfaceUnit,
        index: int,
    ) -> SurfaceResidualSeed:
        return SurfaceResidualSeed(
            residual_kind="rejected_reference_candidate",
            source_ref=unit.source_ref,
            local_discriminator=f"{LENS_ID}::rejected#{index}::{rej.rule_id}",
            rule_id=_RULE_REJECTED,
            reason_code=rej.rule_id,
            payload={
                "rule_id": rej.rule_id,
                "phase": rej.phase,
                "reason": rej.reason,
                "matched_text": rej.matched_text,
                "source_statute_id": rej.source_statute_id,
                "blocking": rej.blocking,
            },
            residual_status="blocked" if rej.blocking else "open",
        )


def _reanchor_binding_to_xml_bytes(
    binding: DefinedTermBinding, xml_bytes: bytes
) -> DefinedTermBinding | None:
    """Re-anchor a binding's ``source_span`` onto its term token in ``xml_bytes``.

    The recognizer reports the binding construct's span as a CHAR offset into the
    decoded body text (``decode_body_text``). The resolver compares a binding's
    byte offset against the USE mention's byte offset (re-anchored into
    ``xml_bytes`` by ``ref_mention_extractor._relocate``), so the two MUST share a
    byte space. The whole construct (e.g.
    ``annetussa asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus)``) seldom
    byte-matches ``xml_bytes`` because element tags sit inside it — but the
    binding's ``term`` (e.g. ``sivutuoteasetus``) is a single token present
    verbatim in ``xml_bytes``.

    BINDING-SITE PROXY: we use the term token's FIRST byte occurrence in
    ``xml_bytes`` as the binding-site offset. The term is recorded in the
    NOMINATIVE as written at the binding site; a later USE is inflected (different
    bytes), so the nominative term's first verbatim occurrence is the binding
    site, not an earlier use. (A bare nominative use that genuinely precedes the
    binding — uncommon for an alias — would make this proxy slightly early; the
    "binding precedes use" guard then errs toward resolving rather than blocking
    that one use. The use-before-binding case for INFLECTED uses stays correctly
    unresolved because the inflected bytes never match the nominative anchor.)

    Returns a binding whose ``source_span.byte_offset`` is the xml_bytes byte
    offset of the term token, or ``None`` when the term token is not found
    verbatim in ``xml_bytes`` (e.g. tag-split term — refuse to guess a position;
    the binding is dropped rather than mis-anchored).
    """
    if binding.source_span is None:
        return None
    term = binding.term.strip()
    if not term:
        return None
    needle = term.encode("utf-8")
    pos = xml_bytes.find(needle)
    if pos < 0:
        return None
    new_span = dataclasses.replace(
        binding.source_span, byte_offset=pos, byte_len=len(needle)
    )
    return dataclasses.replace(binding, source_span=new_span)


def _build_local_defined_term_table(
    xml_bytes: bytes, work_id: str
) -> DefinedTermTable:
    """Build the per-statute defined-term table in ``xml_bytes`` byte space.

    Recognizes binding sites over the decoded body text, then re-anchors each
    binding onto its term token's byte offset in ``xml_bytes`` so the resolver's
    "binding precedes use" ordering compares like-for-like (use offsets are
    ``xml_bytes`` byte offsets). Bindings whose term token does not appear
    verbatim in ``xml_bytes`` are dropped (no mis-anchored position).
    """
    body_text = decode_body_text(xml_bytes)
    if not body_text:
        return build_defined_term_table([])
    bindings = recognize_defined_term_bindings(body_text, source_file=work_id)
    reanchored: list[DefinedTermBinding] = []
    for b in bindings:
        fixed = _reanchor_binding_to_xml_bytes(b, xml_bytes)
        if fixed is not None:
            reanchored.append(fixed)
    return build_defined_term_table(reanchored)


def _default_eu_registry() -> object:
    """The EU nickname registry module (its ``lookup`` is a pure function)."""
    from lawvm.finland.references.registries import eu_nickname

    return eu_nickname


class _EndpointCounts:  # noqa: D106

    """Tiny coverage tally for one mention's resolution endpoint."""

    __slots__ = ("resolved", "ambiguous", "unresolved")

    def __init__(
        self, *, resolved: int = 0, ambiguous: int = 0, unresolved: int = 0
    ) -> None:
        self.resolved = resolved
        self.ambiguous = ambiguous
        self.unresolved = unresolved
