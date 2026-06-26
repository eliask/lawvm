"""ANNOTATION-WITNESS lens — the ``<ref>`` annotation surface as a witness.

grammar7 §13-A. This lens reads each inline AKN ``<ref>`` element in the statute
body and mints ONE ``annotation_reference_witness`` node per element: the
href-resolved target + byte span + displayed surface text. It is the
ADDITIVE, surface_only annotation surface the grammar-vs-annotation comparison
pass (§13-B) contrasts the grammar-induced references against.

CORE PRINCIPLE (grammar7 §2, §9, §10): "delete annotation DEPENDENCE, not
annotation USE." This lens is a SEPARATE emitter — it does NOT feed the grammar
productions and does NOT change reference extraction. The reference lens
(``fi.references.v0``) keeps consuming ``<ref>`` exactly as before (the
explicit_id family is still partly ``<ref>``-dependent on consolidated bodies);
this witness lens runs ALONGSIDE it as a parallel QA/comparison surface. The
eventual retirement of ``<ref>`` consumption is gated on the grammar covering
each family (§10 criteria), NOT on this lane.

A witness node says what the ``<ref>`` markup SAYS — NEVER that the citation is
legally valid or even correct (an unparseable href is recorded as a witness too,
``parsed_ok=False``; an annotation is a fact even when wrong). authority_role is
``candidate`` (a witness, not an asserted surface_fact); surface_only by the
firewall.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
    source_bytes_of,
)
from lawvm.finland.legal_surface.bundle import locate_span
from lawvm.finland.references.cross_refs import (
    AnnotationRefRecord,
    iter_body_annotation_refs,
)

LENS_ID = "fi.annotation_witness.v0"
SCHEMA_VERSION = "v0"

_RULE_WITNESS = "fi.annotation_witness.v0.ref_element"
_RULE_BLOCKED = "fi.annotation_witness.v0.missing_xml_bytes"


def _witness_text_hash(rec: AnnotationRefRecord, index: int) -> str:
    """Content address for a witness whose displayed text can't be char-anchored.

    Keyed off the href, displayed text, and running index so two distinct
    unlocatable witnesses never collide.
    """
    seed = (
        f"{LENS_ID}::unlocatable#{index}|{rec.href}|{rec.displayed_text}"
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _witness_local(index: int) -> str:
    return f"{LENS_ID}::witness#{index}"


class AnnotationWitnessLens:
    """The annotation-witness surface lens (satisfies ``SurfaceLens``)."""

    lens_id: str = LENS_ID
    jurisdiction: str = "fi"
    schema_version: str = SCHEMA_VERSION
    produces_node_kinds: tuple[str, ...] = (
        "annotation_reference_witness",
        "surface_residual",
    )
    produces_edge_kinds: tuple[str, ...] = ()
    required_views: tuple[str, ...] = ("raw_text",)

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult:
        node_seeds: list[SurfaceNodeSeed] = []
        residuals: list[SurfaceResidualSeed] = []

        n_units = 0
        n_witnesses = 0
        n_parsed = 0
        n_unparsed = 0

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
                        local_discriminator=(
                            f"{LENS_ID}::missing_xml::{unit.source_unit_id}"
                        ),
                        rule_id=_RULE_BLOCKED,
                        reason_code="unit_has_no_source_bytes",
                        payload={"source_unit_id": unit.source_unit_id},
                        residual_status="blocked",
                    )
                )
                continue

            cursor = 0
            for rec in iter_body_annotation_refs(bytes(xml_bytes)):
                n_witnesses += 1
                if rec.parsed_ok:
                    n_parsed += 1
                else:
                    n_unparsed += 1
                source_ref, cursor = self._locate(unit, rec, cursor, index)
                node_seeds.append(
                    self._witness_seed(rec, source_ref=source_ref, local=_witness_local(index))
                )
                index += 1

        coverage: dict[str, object] = {
            "units": n_units,
            "witnesses": n_witnesses,
            "parsed_hrefs": n_parsed,
            "unparsed_hrefs": n_unparsed,
            "residuals": len(residuals),
        }
        return SurfaceLensResult(
            lens_id=LENS_ID,
            node_seeds=tuple(node_seeds),
            edge_seeds=(),
            residuals=tuple(residuals),
            diagnostics=(),
            coverage=coverage,
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _locate(
        unit: SourceSurfaceUnit,
        rec: AnnotationRefRecord,
        cursor: int,
        index: int,
    ) -> tuple[SourceSpanRef, int]:
        """Char-anchor the witness's displayed text in ``raw_text``.

        Advances a left-to-right cursor so repeated identical surfaces map to
        successive occurrences (retrying from 0 once). When the displayed text
        cannot be char-located (empty surface, or a markup-split phrase absent
        from the ``<p>`` coordinate space), synthesize a DEGENERATE zero-length
        span at the unit origin — the authoritative byte span always rides the
        payload, so the lossy char fallback never contaminates the witness's
        identity. A witness is never dropped to a residual: every ``<ref>`` mints
        a node (fail-loud by totality).
        """
        surface = rec.displayed_text or ""
        if surface:
            ref, next_cursor = locate_span(unit, surface, cursor=cursor)
            if ref is not None:
                return ref, next_cursor
            ref, _ = locate_span(unit, surface, cursor=0)
            if ref is not None:
                return ref, cursor
        return (
            SourceSpanRef(
                source_unit_id=unit.source_unit_id,
                source_hash=unit.source_hash,
                work_id=unit.work_id,
                address=unit.address,
                char_start=0,
                char_end=0,
                text_hash=_witness_text_hash(rec, index),
            ),
            cursor,
        )

    @staticmethod
    def _witness_seed(
        rec: AnnotationRefRecord,
        *,
        source_ref: SourceSpanRef,
        local: str,
    ) -> SurfaceNodeSeed:
        payload: dict[str, object] = {
            "annotation_kind": "akn_ref",
            "href": rec.href,
            "displayed_text": rec.displayed_text,
            "parsed_ok": rec.parsed_ok,
            "target_id": rec.target_statute_id or None,
            "target_section": rec.target_section or None,
            "source_section": rec.source_section or None,
            # Authoritative byte span of the inner phrase into xml_bytes — kept
            # separate from the char-coord source_ref (the graph's own coordinate).
            "source_span_byte_offset": rec.source_byte_offset,
            "source_span_len": rec.source_byte_len,
        }
        # A witness's status is structural PRESENCE, not a resolution outcome — the
        # annotation is a surface fact, never an assertion about its target.
        return SurfaceNodeSeed(
            node_kind="annotation_reference_witness",
            source_ref=source_ref,
            local_discriminator=local,
            rule_id=_RULE_WITNESS,
            node_status="present",
            payload=payload,
            authority_role="candidate",
        )
