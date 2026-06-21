"""Build a SourceSurfaceBundle from a Finnish statute's XML (Pro r5 Phase 1).

The substrate rule (§D4): lenses read ONLY from the bundle, never fetch source
themselves. For v0 we expose ONE whole-body unit per statute:

  * ``raw_text``  — the decoded body text (``<p>`` content, newline-joined), the
    coordinate space SourceSpanRef char offsets index into.
  * ``source_bytes`` (typed unit field, read via ``source_bytes_of``) — the raw
    XML, so adapter lenses can still run the existing recognizers (the AKN
    ``<ref>`` lane genuinely needs the XML tree; Pro r5 endorses "assembler first
    with minimal substrate; v0 lenses may tokenize internally"). This is the
    Stage-1 bridge, not a permanent fixture.

``locate_span`` is the shared helper every adapter uses to turn a recognizer's
matched ``surface_text`` into a SourceSpanRef anchored in ``raw_text`` (a
left-to-right cursor handles repeated identical surfaces).
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import SourceSurfaceBundle, SourceSurfaceUnit
from lawvm.core.legal_surface_tokens import SegmentationGraph
from lawvm.core.source_witness import DigestWitness, SourceWitness
from lawvm.core.stage_result import (
    NEUTRAL_AUTHORITY,
    CoverageCertificate,
    EvidenceBundle,
    Residual,
    StageResult,
)
from lawvm.finland.legal_surface.clause_segment import (
    build_clause_index,
    build_segmentation_graph,
)
from lawvm.finland.legal_surface.provision_index import build_provision_index
from lawvm.finland.legal_surface.tokenize import (
    build_morph_overlay,
    build_token_tape,
)

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode_body_text(xml_bytes: bytes) -> str:
    """Decode the statute body into a single coordinate space.

    Concatenates each ``<p>`` element's text (``itertext``) joined by newlines —
    the same paragraph set the existing reference lanes scan, so a surface that a
    recognizer matched in a ``<p>`` is locatable here. Returns "" on parse error.
    """
    if not xml_bytes:
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    parts: list[str] = []
    for el in root.iter():
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == "p":
            parts.append("".join(el.itertext()))
    return "\n".join(parts)


def _segmentation_coverage(seg: SegmentationGraph) -> CoverageCertificate:
    """Map the SegmentationGraph's exact ``[0, text_len)`` partition onto a typed
    :class:`CoverageCertificate` (the token/source-unit waist's returned account).

    The SegmentationGraph already partitions the whole body text EXACTLY (its
    ``__post_init__`` enforces contiguous, gap-free, non-overlapping coverage —
    see ``core/legal_surface_tokens.py``). This function does NOT recompute an
    invariant; it READS OFF the existing partition: every non-``residual``
    segment's chars are ``owned``; every ``residual`` (``benign_whitespace``)
    segment's chars are ``residual``. ``benign``/``violation`` are 0 (whitespace
    is the only residue today, and it is proven-benign typed residue, not a
    signal-bearing violation). ``is_partition()`` holds because owned + residual
    == text_len by construction.
    """
    owned = 0
    residual = 0
    for seg_span in seg.segments:
        span = seg_span.char_end - seg_span.char_start
        if seg_span.kind == "residual":
            residual += span
        else:
            owned += span
    return CoverageCertificate(
        unit="chars",
        total=seg.text_len,
        owned=owned,
        benign=0,
        residual=residual,
        violation=0,
    )


def _segmentation_residuals(
    seg: SegmentationGraph,
    raw_text: str,
) -> tuple[Residual, ...]:
    """One typed :class:`Residual` per benign-whitespace residual segment.

    Each residual span is proven-benign (whitespace gaps the segmentation owns
    explicitly), so ``blocking=False`` — it must NOT forbid a clean claim. The
    span text is carried verbatim (self-evidencing) along with its char offsets.
    """
    residuals: list[Residual] = []
    for seg_span in seg.segments:
        if seg_span.kind != "residual":
            continue
        residuals.append(
            Residual(
                kind="benign_uninterpreted",
                reason="segmentation_benign_whitespace",
                scope=seg.source_unit_id,
                source_unit_id=seg.source_unit_id,
                char_start=seg_span.char_start,
                char_end=seg_span.char_end,
                text=raw_text[seg_span.char_start : seg_span.char_end],
                blocking=False,
            )
        )
    return tuple(residuals)


def build_surface_bundle(
    xml_bytes: bytes,
    statute_id: str,
    *,
    surface_time: str | None = None,
    language: str = "fi",
) -> SourceSurfaceBundle:
    """Build a whole-body SourceSurfaceBundle for one Finnish statute (v0).

    Thin value-only wrapper over :func:`build_surface_bundle_staged` so the tool
    consumers (``fi_parse_view.py``, ``bill_analysis.py``) are untouched: they get
    the same ``SourceSurfaceBundle``. Consumers that want the returned coverage /
    residual / evidence account call the staged form directly.
    """
    return build_surface_bundle_staged(
        xml_bytes,
        statute_id,
        surface_time=surface_time,
        language=language,
    ).value


def build_surface_bundle_staged(
    xml_bytes: bytes,
    statute_id: str,
    *,
    surface_time: str | None = None,
    language: str = "fi",
) -> StageResult[SourceSurfaceBundle]:
    """Build the whole-body bundle AND return its token/source-unit account.

    The token/source-unit waist of the StageResult endgame (spine
    ``notes_internal/STAGERESULT_ENDGAME.md`` row #2). The ``value`` is the
    identical :class:`SourceSurfaceBundle` :func:`build_surface_bundle` always
    produced; the four accounts SURFACE the partition the bundle already embeds:

      * ``coverage`` — the SegmentationGraph's exact ``[0, text_len)`` char
        partition projected onto a :class:`CoverageCertificate`
        (``is_partition()`` holds);
      * ``residuals`` — one benign-whitespace :class:`Residual` per residual
        segment (``blocking=False``);
      * ``evidence`` — the body source hash as a typed
        :class:`SourceWitness`/:class:`DigestWitness` footing;
      * ``findings`` / ``authority`` — identity defaults (segmentation emits no
        findings; a source unit is evidence footing, not replay authority).

    This is additive: nothing in the value path changes, so it is bench 0-delta.
    """
    raw_text = decode_body_text(xml_bytes)
    source_hash = _sha256_bytes(xml_bytes)
    text_hash = _sha256_text(raw_text)
    source_unit_id = f"{statute_id}#body"
    token_tape = build_token_tape(source_unit_id, raw_text)
    segmentation_graph = build_segmentation_graph(source_unit_id, raw_text)
    # Thread the explicit consolidated-as-of date (``surface_time``) onto the
    # unit's effective_interval START so a by-name citation resolves to the act
    # version in force WHILE this consolidated body held (static-as-of-citing);
    # see ``lenses/references.py::_unit_validity_interval`` +
    # ``resolve_mentions(use_mention_validity=True)``. This is the consolidated
    # VERSION date the text holds at — NOT the citing statute's enactment year —
    # so a body legitimately citing a post-enactment version resolves correctly.
    # When ``surface_time`` is absent the interval stays open ``(None, None)`` and
    # a multi-version name stays AMBIGUOUS downstream (fail-loud, never a guessed
    # "now"). Only the START is set: an open right edge means "still in force as
    # far as this text knows", which is the correct upper bound for a snapshot.
    effective_interval: tuple[str | None, str | None] = (surface_time, None)
    unit = SourceSurfaceUnit(
        source_unit_id=source_unit_id,
        work_id=statute_id,
        address=None,
        raw_text=raw_text,
        source_hash=source_hash,
        effective_interval=effective_interval,
        source_ref=SourceSpanRef(
            source_unit_id=source_unit_id,
            source_hash=source_hash,
            work_id=statute_id,
            address=None,
            char_start=0,
            char_end=len(raw_text),
            text_hash=text_hash,
        ),
        # Stage-1 bridge: adapter lenses run the existing recognizers, which need
        # the XML tree (the <ref> lane especially). Carried as the TYPED
        # ``source_bytes`` field below (read via ``source_bytes_of``), not a
        # free-form ``metadata`` key. Removed once those lenses migrate to
        # token_tape views (Phase 7).
        #
        # SegmentationGraph (additive structural substrate, one level above the
        # clause index in the SourceSyntaxGraph stack): classifies the body into
        # heading / chapeau / list_item (with chapeau inheritance) /
        # quoted_amendment_block / prose + explicit residual spans, partitioning
        # the text exactly (no silent drop). Attached via ``metadata`` rather than
        # a new unit field so the substrate stays additive without touching the
        # universal unit schema — like ``xml_bytes`` it is a view a later pass
        # consumes, NOT a graph input (the assembler's graph_id is computed over
        # node/edge payloads + subject only, never over unit metadata, so this
        # cannot perturb the assembled surface graph).
        # ProvisionIndex (additive provision-boundary substrate): maps each body
        # paragraph's char range to its enclosing AKN provision (§/momentti/kohta
        # + eId), recovered from the structure the body decode drops. Attached via
        # ``metadata`` like ``segmentation_graph`` so it stays additive without
        # touching the universal unit schema (and, like the segmentation graph, it
        # is a unit view the assembler's graph_id never folds in — it cannot
        # perturb the assembled surface graph). It unblocks enclosing-section
        # anaphora + span-scoped composition: a consumer queries
        # ``provision_index.provision_at(char_start, char_end)``.
        metadata={
            "segmentation_graph": segmentation_graph,
            "provision_index": build_provision_index(
                xml_bytes,
                source_unit_id,
                body_text=raw_text,
                text_hash=text_hash,
            ),
        },
        # §D4 Stage-1 bridge: the raw AKN XML as a TYPED unit view. Adapter
        # lenses (references / annotation_witness / definitions) read it via
        # ``source_bytes_of`` to re-parse the markup tree, instead of the former
        # untyped ``metadata["xml_bytes"]`` channel. Additive: like the token
        # tape it is a view the assembler's graph_id never folds in.
        source_bytes=xml_bytes,
        # Phase 7 (§D4): populate the source-preserving token view additively.
        # Lenses that ignore it are unaffected; token-consuming lenses set
        # required_views=("token_tape",).
        token_tape=token_tape,
        # Phase 7 (§D4): sparse reverse-morphology overlay over the tape. Covers
        # ONLY the closed known-head vocabulary (lemma index inverts M1); an
        # absent annotation means "unknown", never "no lemma exists". Cheap: the
        # default lemma index is memoized.
        morph_overlay=build_morph_overlay(token_tape),
        # Clause-segmentation substrate: deterministic sentence/clause index over
        # the same coordinate space. Additive — unconsumed by v0 lenses, and the
        # graph_id is computed over node/edge payloads only (never unit views), so
        # this cannot perturb the assembled graph. Later attachment passes query
        # it instead of the magic colocation window.
        clause_index=build_clause_index(
            source_unit_id, raw_text, token_tape=token_tape
        ),
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id=statute_id,
        scope={"kind": "whole_work"},
        surface_time=surface_time,
        source_bundle_hash=source_hash,
        language=language,
    )
    bundle = SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))
    return StageResult(
        value=bundle,
        evidence=EvidenceBundle(
            (
                SourceWitness(
                    source_role="statute_body_source",
                    artifact_id=statute_id,
                    source_unit_id=source_unit_id,
                    digest=DigestWitness("sha256", source_hash),
                ),
            )
        ),
        residuals=_segmentation_residuals(segmentation_graph, raw_text),
        coverage=_segmentation_coverage(segmentation_graph),
        authority=NEUTRAL_AUTHORITY,
    )


def locate_span(
    unit: SourceSurfaceUnit,
    surface_text: str,
    *,
    cursor: int = 0,
) -> tuple[SourceSpanRef | None, int]:
    """Locate ``surface_text`` in ``unit.raw_text`` from ``cursor`` (char space).

    Returns ``(SourceSpanRef | None, next_cursor)``. Repeated identical surfaces
    map left-to-right via the returned cursor. Fail-loud by absence: an
    unlocatable surface yields ``(None, cursor)`` — never a fabricated offset.
    """
    if not surface_text:
        return None, cursor
    start = unit.raw_text.find(surface_text, cursor)
    if start < 0:
        return None, cursor
    end = start + len(surface_text)
    ref = SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=start,
        char_end=end,
        text_hash=_sha256_text(surface_text),
    )
    return ref, end


def span_ref_at(
    unit: SourceSurfaceUnit,
    char_start: int,
    char_end: int,
) -> SourceSpanRef | None:
    """Build a SourceSpanRef for an explicit char range in ``unit.raw_text``.

    Unlike :func:`locate_span`, which searches for a surface string, this anchors
    on a range a recognizer already matched in the SAME coordinate space the
    bundle's ``raw_text`` defines (the §D4 unit). It is the truthful anchor for a
    fact whose surface string does not round-trip through ``str.find`` — e.g. a
    recognizer that normalised whitespace in its captured surface but reported the
    exact offsets of the construct it matched. The text_hash addresses the ACTUAL
    sliced text at those offsets, so the ref stays content-addressed and never
    fabricates an offset: an out-of-range request yields ``None`` (fail-loud).
    """
    n = len(unit.raw_text)
    if char_start < 0 or char_end > n or char_start >= char_end:
        return None
    sliced = unit.raw_text[char_start:char_end]
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=char_start,
        char_end=char_end,
        text_hash=_sha256_text(sliced),
    )
