"""PDF-spine base-loader fallback (FI PDF spine Phase 1).

Some Finnish statutes carry NO operative body in their base ``main.xml`` — the
``<body>`` is an ``hcontainer``-only metadata wrapper (label + pointer + display
prose), and the actual ``N §`` … statute spine lives ONLY in an attachment PDF
(design ``FI_ATTACHMENT_HIERARCHY_DESIGN``: XML attachments carry 0% inline
spine; the whole hierarchical premise routes through the PDF). The pilot is
``2011/38`` (Vna ilmanlaadusta), whose ``5916.pdf`` exposes a clean ``1 §`` …
``24 §`` spine that Phase 0's recogniser (:mod:`lawvm.finland.attachment_ir`
spine mode) already promotes to real ``SECTION``/``CHAPTER``/``SUBSECTION``/
``ITEM`` IR.

This module is the Phase-1 base-loader hook: when the base body is
non-substantial (no ``SECTION``/``PARAGRAPH`` in its IR) AND a ``fin``
attachment PDF exists AND the spine recogniser yields at least one ``SECTION``,
:func:`build_pdf_spine_base_ir` returns a graftable base IR derived from the
PDF. Replay's structure graft resolves section-targeted ops against
``SECTION`` nodes by ``.label`` in the IR tree, so this spine base is directly
addressable.

Authority-lane discipline (design §"SourceLaneSelectionEvidence"). The
PDF-derived base is a **distinct, lower-authority source lane**: it is
materialised ONLY when the XML base is non-substantial, so a substantial XML
base is structurally NEVER overridden. The synthesised root records
``attrs[BASE_SOURCE_LANE_KEY] = PDF_SPINE_LANE`` plus the attachment provenance
(``attrs["base_source_pdf"]``) so the lane is honest (§1.8) and any downstream
lane-selection can see that this base is a fallback, not an authoritative XML
body.

Purity: :func:`spine_base_ir_from_pdf_text` is a pure ``str -> IRNode | None``
transform (no I/O). :func:`build_pdf_spine_base_ir` performs exactly one bytes
read (``cs.read_attachment_media``) and one ``pdftotext`` extraction, guarded
behind the non-substantial predicate so the common substantial-XML path pays
nothing.
"""

from __future__ import annotations

from typing import Optional, Protocol

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


class _AttachmentMediaReader(Protocol):
    """Structural type for the one store capability the hook needs.

    Narrowing to a protocol (rather than the concrete ``CorpusStore`` ABC)
    keeps the base-loader helper decoupled from the store hierarchy: any object
    exposing ``read_attachment_media`` satisfies it, so both Finnish backends —
    and test doubles — bind without importing the ABC.
    """

    def read_attachment_media(self, sid: str, filename: str) -> bytes | None: ...

# Attribute keys / values naming the base source lane. Kept as module
# constants so the store hook, the pipeline wiring, and the tests agree on one
# spelling (no stringly-typed drift).
BASE_SOURCE_LANE_KEY = "base_source_lane"
PDF_SPINE_LANE = "pdf_spine"
XML_BODY_LANE = "xml_body"


def base_ir_is_substantial(base_ir: IRNode) -> bool:
    """Return True iff the base IR carries an operative statute body.

    A body is *substantial* iff its IR tree contains at least one ``SECTION``
    or ``PARAGRAPH`` node — mirroring ``scripts/curate_corpus.check_xml_structure``
    (``<section>``/``<paragraph>`` present ⇒ ``ok``, else ``hcontainer``) but
    operating on the already-built IR rather than re-scanning XML bytes. An
    ``hcontainer``-only metadata wrapper (the ``2011/38`` shape) has neither and
    is therefore *non-substantial* — the only case in which the PDF-spine
    fallback may fire.
    """
    for node in _iter_tree(base_ir):
        if node.kind in (IRNodeKind.SECTION, IRNodeKind.PARAGRAPH):
            return True
    return False


def spine_base_ir_from_pdf_text(
    pdf_text: str,
    *,
    source_ref: str = "",
    pdf_name: str = "",
) -> Optional[IRNode]:
    """Pure transform: attachment-PDF text → graftable spine base IR (or None).

    Runs the Phase-0 statute-spine recogniser
    (:func:`lawvm.finland.attachment_ir.pdf_text_to_ir_node`, which auto-detects
    spine mode). Returns the spine IR — wrapped in a ``body`` root so its shape
    matches the normal ``fi_xml_to_ir_node`` base (``body > … > section``) — and
    tagged as the PDF-spine source lane, ONLY when the recogniser actually
    produced at least one ``SECTION``. If the PDF is not spine-shaped (tables,
    prose, budget mojibake) the recogniser stays in appendix mode and yields no
    ``SECTION``; this returns None and the caller keeps the (non-substantial)
    XML base untouched — no invented spine (§1.8).
    """
    # Local import: attachment_ir pulls in pdf_layout/pdf_text lazily; keep this
    # module importable without those optional deps until a spine is requested.
    from lawvm.finland.attachment_ir import pdf_text_to_ir_node

    spine = pdf_text_to_ir_node(pdf_text, source_ref=source_ref)
    if not _has_section(spine):
        return None

    root_attrs = {
        BASE_SOURCE_LANE_KEY: PDF_SPINE_LANE,
        "spine_mode": True,
    }
    if pdf_name:
        root_attrs["base_source_pdf"] = pdf_name
    if source_ref:
        root_attrs["source_ref"] = source_ref
    # Wrap the spine hcontainer under a ``body`` root so the base IR root kind
    # matches the ordinary XML-derived base (``body`` root); the ``.label``
    # graft walks the whole tree, so nesting depth is irrelevant to targeting,
    # but a consistent root keeps projection/serialisation uniform.
    return IRNode(
        kind=IRNodeKind.BODY,
        label=None,
        attrs=root_attrs,
        children=(spine,),
    )


def build_pdf_spine_base_ir(
    cs: _AttachmentMediaReader,
    sid: str,
    base_ir: IRNode,
    base_xml_bytes: bytes,
) -> Optional[IRNode]:
    """Materialise a PDF-spine base IR for ``sid`` when the XML base is empty.

    Fires ONLY when ALL hold (else returns None, leaving the XML base as-is):

    1. ``base_ir`` is non-substantial (no ``SECTION``/``PARAGRAPH``) — a
       substantial XML base is a hard non-fire, so this fallback can never
       override an authoritative body (lower-authority lane discipline).
    2. The base XML links exactly one or more ``fin`` attachment PDF, fetchable
       via ``cs.read_attachment_media``.
    3. The Phase-0 spine recogniser yields at least one ``SECTION`` from the PDF
       text (spine-shaped; not a table/prose/mojibake appendix).

    On success returns a ``body``-rooted spine IR tagged with the PDF-spine
    source lane (see :func:`spine_base_ir_from_pdf_text`). The first spine-shaped
    attachment wins (statutes with a PDF-only body carry a single operative
    attachment; the pilot ``2011/38`` has exactly ``5916.pdf``).
    """
    if base_ir_is_substantial(base_ir):
        return None

    # Lazy imports (heavy / optional deps) — only reached on the rare
    # non-substantial base, never on the substantial-XML hot path.
    from lawvm.finland.attachment_ir import extract_attachment_pdf_links
    from lawvm.finland.pdf_text import pdf_to_text

    links = extract_attachment_pdf_links(base_xml_bytes)
    if not links:
        return None

    for link in links:
        pdf_bytes = cs.read_attachment_media(sid, link.pdf_name)
        if not pdf_bytes:
            continue
        text = pdf_to_text(pdf_bytes)
        if not text:
            continue
        source_ref = f"finlex://sd/{sid}/fin/media/{link.pdf_name}"
        spine = spine_base_ir_from_pdf_text(
            text, source_ref=source_ref, pdf_name=link.pdf_name
        )
        if spine is not None:
            return spine
    return None


# ---------------------------------------------------------------------------
# Tree helpers (small, co-located; mirror attachment_ir.iter_tree).
# ---------------------------------------------------------------------------


def _iter_tree(node: IRNode):
    yield node
    for child in node.children:
        yield from _iter_tree(child)


def _has_section(node: IRNode) -> bool:
    return any(n.kind is IRNodeKind.SECTION for n in _iter_tree(node))
