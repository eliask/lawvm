"""Build a SourceSurfaceBundle from a Finnish statute's XML (Pro r5 Phase 1).

The substrate rule (§D4): lenses read ONLY from the bundle, never fetch source
themselves. For v0 we expose ONE whole-body unit per statute:

  * ``raw_text``  — the decoded body text (``<p>`` content, newline-joined), the
    coordinate space SourceSpanRef char offsets index into.
  * ``metadata["xml_bytes"]`` — the raw XML, so adapter lenses can still run the
    existing recognizers (the AKN ``<ref>`` lane genuinely needs the XML tree;
    Pro r5 endorses "assembler first with minimal substrate; v0 lenses may
    tokenize internally"). This is the Stage-1 bridge, not a permanent fixture.

``locate_span`` is the shared helper every adapter uses to turn a recognizer's
matched ``surface_text`` into a SourceSpanRef anchored in ``raw_text`` (a
left-to-right cursor handles repeated identical surfaces).
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import SourceSurfaceBundle, SourceSurfaceUnit
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


def build_surface_bundle(
    xml_bytes: bytes,
    statute_id: str,
    *,
    surface_time: str | None = None,
    language: str = "fi",
) -> SourceSurfaceBundle:
    """Build a whole-body SourceSurfaceBundle for one Finnish statute (v0)."""
    raw_text = decode_body_text(xml_bytes)
    source_hash = _sha256_bytes(xml_bytes)
    text_hash = _sha256_text(raw_text)
    source_unit_id = f"{statute_id}#body"
    token_tape = build_token_tape(source_unit_id, raw_text)
    unit = SourceSurfaceUnit(
        source_unit_id=source_unit_id,
        work_id=statute_id,
        address=None,
        raw_text=raw_text,
        source_hash=source_hash,
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
        # the XML tree (the <ref> lane especially). Removed once lenses migrate
        # to token_tape views (Phase 7).
        metadata={"xml_bytes": xml_bytes},
        # Phase 7 (§D4): populate the source-preserving token view additively.
        # Lenses that ignore it are unaffected; token-consuming lenses set
        # required_views=("token_tape",).
        token_tape=token_tape,
        # Phase 7 (§D4): sparse reverse-morphology overlay over the tape. Covers
        # ONLY the closed known-head vocabulary (lemma index inverts M1); an
        # absent annotation means "unknown", never "no lemma exists". Cheap: the
        # default lemma index is memoized.
        morph_overlay=build_morph_overlay(token_tape),
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id=statute_id,
        scope={"kind": "whole_work"},
        surface_time=surface_time,
        source_bundle_hash=source_hash,
        language=language,
    )
    return SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))


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
