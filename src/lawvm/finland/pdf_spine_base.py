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

from typing import Optional, Protocol, cast

import lxml.etree as etree

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


# ---------------------------------------------------------------------------
# XML serialisation of the PDF spine (FI PDF spine Phase 2, Option B).
#
# The IRNode replay fold resolves section-targeted ops by ``.label`` against
# ``SECTION`` nodes in the IR tree (Phase 1) — it never needs XML. But the
# oracle-comparison / locator path (:mod:`lawvm.finland.section_resolver`
# ``FinnishAKNResolver`` + :mod:`lawvm.tools.oracle_text`) walks an **XML**
# root: it matches an eId with ``.//*[@eId="…"]`` and falls back to comparing
# ``<section>/<num>`` text. Serialising the spine IR to AKN XML with the
# canonical Finlex ``part_N__chp_N__sec_N`` eId scheme (the same convention
# ``section_resolver._ABBREV`` uses) lets that path resolve against the
# PDF-derived base too, not only via the ``.label`` graft.
#
# This is a pure ``IRNode -> bytes`` transform (no I/O). It is additive: it
# introduces no new load-path behaviour on its own; a caller that wants the
# XML view of the spine base calls :func:`spine_ir_to_akn_xml_bytes`.
# ---------------------------------------------------------------------------

# AKN 3.0 namespace — the Finlex encoding. The resolver matches sections
# namespace-agnostically (``.//{*}section``) and reads the plain ``eId``
# attribute, so emitting under this namespace resolves identically to the
# real consolidated XML the oracle path normally parses.
AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def spine_eid_of(node: IRNode) -> Optional[str]:
    """Return the canonical Finlex eId recorded on a spine structural node.

    Phase 0 records ``attrs["eid"]`` on every ``CHAPTER``/``SECTION`` it emits
    (``chp_N`` / ``sec_N`` / ``chp_M__sec_N``). This accessor is the single
    read point so the serialiser and any downstream consumer agree on the key.
    """
    eid = node.attrs.get("eid")
    return eid if isinstance(eid, str) and eid else None


def _akn(tag: str) -> str:
    return f"{{{AKN_NS}}}{tag}"


def _num_text_for(node: IRNode) -> str:
    """The ``<num>`` text for a spine structural node.

    Mirrors the Finlex convention the resolver's ``<num>``-text fallback
    (:meth:`FinnishAKNResolver._find_by_num_text`) normalises against:
    ``N §`` for a section, ``N luku`` for a chapter, ``N)`` for a kohta item,
    bare ``N`` for a positional subsection.
    """
    label = node.label or ""
    if node.kind is IRNodeKind.SECTION:
        return f"{label} §"
    if node.kind is IRNodeKind.CHAPTER:
        return f"{label} luku"
    if node.kind is IRNodeKind.ITEM:
        return f"{label})"
    return label


def _append_text_content(parent: etree._Element, node: IRNode) -> None:
    """Emit a ``<content><p>…</p></content>`` child carrying ``node.text``.

    AKN leaf text lives in a ``<content>`` wrapper; the resolver ignores it
    (it matches on ``<num>``/eId), but serialising it keeps the XML a faithful
    view of the spine for oracle text comparison and ``lawvm show``.
    """
    if not node.text:
        return
    content = etree.SubElement(parent, _akn("content"))
    p = etree.SubElement(content, _akn("p"))
    p.text = node.text


def _spine_node_to_xml(node: IRNode, parent: etree._Element) -> None:
    """Recursively serialise one spine IR node into ``parent`` (AKN element).

    Structural nodes (``CHAPTER``/``SECTION``/``SUBSECTION``/``ITEM``) become
    AKN elements carrying an ``eId`` (from ``attrs["eid"]`` where present, else
    derived) and a ``<num>`` head; ``HEADING`` becomes ``<heading>``; leaf text
    becomes a ``<content><p>``. Unknown kinds (``P``, banners) fall through to
    a plain ``<p>`` so no text is dropped (§0 total-accounting).
    """
    kind = node.kind
    if kind is IRNodeKind.CHAPTER:
        el = etree.SubElement(parent, _akn("chapter"))
        eid = spine_eid_of(node) or f"chp_{node.label}"
        el.set("eId", eid)
        etree.SubElement(el, _akn("num")).text = _num_text_for(node)
        for child in node.children:
            _spine_node_to_xml(child, el)
        return

    if kind is IRNodeKind.SECTION:
        el = etree.SubElement(parent, _akn("section"))
        eid = spine_eid_of(node) or f"sec_{node.label}"
        el.set("eId", eid)
        etree.SubElement(el, _akn("num")).text = _num_text_for(node)
        for child in node.children:
            _spine_node_to_xml(child, el)
        return

    if kind is IRNodeKind.SUBSECTION:
        el = etree.SubElement(parent, _akn("subsection"))
        parent_eid = parent.get("eId")
        if parent_eid and node.label:
            el.set("eId", f"{parent_eid}__subsec_{node.label}")
        if node.label:
            etree.SubElement(el, _akn("num")).text = _num_text_for(node)
        _append_text_content(el, node)
        for child in node.children:
            _spine_node_to_xml(child, el)
        return

    if kind is IRNodeKind.ITEM:
        el = etree.SubElement(parent, _akn("point"))
        parent_eid = parent.get("eId")
        if parent_eid and node.label:
            el.set("eId", f"{parent_eid}__list_{node.label}")
        etree.SubElement(el, _akn("num")).text = _num_text_for(node)
        _append_text_content(el, node)
        for child in node.children:
            _spine_node_to_xml(child, el)
        return

    if kind is IRNodeKind.HEADING:
        if node.text:
            etree.SubElement(parent, _akn("heading")).text = node.text
        return

    # Preamble / banner / anything else: a plain <p> so its text is owned.
    if node.text:
        etree.SubElement(parent, _akn("p")).text = node.text
    for child in node.children:
        _spine_node_to_xml(child, parent)


def spine_ir_to_akn_element(spine_ir: IRNode) -> "etree._Element":
    """Serialise a ``body``-rooted spine IR to an AKN ``<body>`` element.

    Accepts the exact shape :func:`spine_base_ir_from_pdf_text` returns
    (``BODY > HCONTAINER > (P* CHAPTER* SECTION*)``) — or a bare ``HCONTAINER``
    root — and produces an AKN ``<body>`` whose ``<section>`` elements carry
    the canonical ``sec_N`` / ``chp_M__sec_N`` eIds and ``<num>N §</num>``
    heads. The PDF-spine source lane attr is re-emitted on the ``<body>`` as a
    ``source`` marker so the serialised view stays honest about provenance.
    """
    body = etree.Element(_akn("body"))
    lane = spine_ir.attrs.get(BASE_SOURCE_LANE_KEY)
    if isinstance(lane, str) and lane:
        body.set("data-base-source-lane", lane)
    pdf = spine_ir.attrs.get("base_source_pdf")
    if isinstance(pdf, str) and pdf:
        body.set("data-base-source-pdf", pdf)

    # Descend into the spine content. The body root wraps a single HCONTAINER
    # (the spine root); sections/chapters live directly under it. We emit the
    # section/chapter subtree flat under <body> (matching the consolidated FI
    # encoding, where <section> elements are body children), skipping the
    # HCONTAINER wrapper itself but keeping its preamble <p> nodes.
    for top in _spine_roots(spine_ir):
        _spine_node_to_xml(top, body)
    return body


def _spine_roots(spine_ir: IRNode):
    """Yield the structural children to serialise under ``<body>``.

    Unwraps the ``BODY``/``HCONTAINER`` wrapper(s) so ``<section>``/``<chapter>``
    land directly under ``<body>`` (the consolidated FI shape). If the spine is
    already a bare HCONTAINER or a naked section list, we still descend one wrap
    level so the caller need not know the exact envelope.
    """
    node = spine_ir
    if node.kind is IRNodeKind.BODY and len(node.children) == 1:
        node = node.children[0]
    if node.kind is IRNodeKind.HCONTAINER:
        yield from node.children
        return
    # Fallback: treat the node's children as the roots, or the node itself.
    if node.children:
        yield from node.children
    else:
        yield node


def spine_ir_to_akn_xml_bytes(spine_ir: IRNode) -> bytes:
    """Serialise a spine IR to an AKN ``akomaNtoso`` document (UTF-8 bytes).

    Pure ``IRNode -> bytes``. The returned document is a minimal but
    resolver-complete AKN wrapper: ``<akomaNtoso><act><body>…</body></act>``
    with ``<section eId="sec_N"><num>N §</num>…`` inside, so both
    ``FinnishAKNResolver.resolve`` (eId / suffix / versioned match) and
    ``resolve_raw`` (``<num>``-text match) resolve against it exactly as they
    do against real consolidated Finlex XML.
    """
    # Declare AKN as the default namespace so the document reads like real
    # Finlex XML. lxml accepts a ``None`` prefix key at runtime; the stub types
    # it as ``Mapping[str, str]`` only, hence the cast.
    nsmap = cast("dict[str, str]", {None: AKN_NS})
    root = etree.Element(_akn("akomaNtoso"), nsmap=nsmap)
    act = etree.SubElement(root, _akn("act"))
    body = spine_ir_to_akn_element(spine_ir)
    act.append(body)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)
