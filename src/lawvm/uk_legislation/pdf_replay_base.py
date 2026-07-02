"""pdf_replay_base.py — admit a PDF-derived UK Act as a replay base.

The UK replay engine (:func:`uk_legislation.replay_executor.replay_uk_ops`) takes
its enacted starting point as an :class:`~lawvm.core.ir.IRStatute`: a ``BODY``
tree of provisions plus ``supplements`` (the Act's schedules, lifted out of the
body), the shape the XML loader
(:func:`uk_legislation.uk_grafter.parse_uk_statute_ir`) produces.

For the ~7,547 UK Acts that exist upstream *only* as PDF (metadata-only XML stub,
``NumberOfProvisions="0"``), there is no XML body to load.  This module is the
PDF analogue of the XML loader's ``_build_ir_from_root``: it takes the
PDF-derived body :class:`~lawvm.core.ir.IRNode` (from
:func:`uk_legislation.pdf_grammar.uk_layout_to_ir`, side-notes already segmented
onto sections as headings) and re-shapes it into a **replay-admissible**
``IRStatute`` — schedules split into ``supplements`` exactly as the XML path
does, so downstream UK replay consumes a PDF-sourced base *identically* to an
XML-sourced one.

Correctness discipline
----------------------
Structural equivalence with the XML path (for replay purposes) is asserted by a
golden-equivalence test (``tests/test_uk_pdf_replay_base.py``): a hand-authored
Act, expressed once as CLML XML and once as a PDF layout, must yield the same
replay-normative statute shape.  The ``source_lane="pdf"`` metadata marks the
base as PDF-sourced (a lower-authority provenance) without changing its replay
shape.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind


def _split_body_and_schedules(
    body: IRNode,
) -> Tuple[IRNode, Tuple[IRNode, ...]]:
    """Lift top-level ``SCHEDULE`` children out of the body into supplements.

    The PDF grammar appends schedule subtrees as direct children of the body (it
    walks a single linear text stream).  The XML loader instead returns the body
    free of schedules and carries them in ``IRStatute.supplements``.  To make the
    PDF base replay-identical, we move every top-level SCHEDULE child of the body
    into the supplements tuple, preserving order; non-schedule children stay in
    the body.
    """
    kept: List[IRNode] = []
    schedules: List[IRNode] = []
    for child in body.children:
        if child.kind is IRNodeKind.SCHEDULE:
            schedules.append(child)
        else:
            kept.append(child)
    if len(kept) == len(body.children):
        return body, ()
    new_body = IRNode(
        kind=body.kind,
        label=body.label,
        text=body.text,
        attrs=dict(body.attrs),
        children=tuple(kept),
    )
    return new_body, tuple(schedules)


def pdf_ir_to_replay_base(
    body: IRNode,
    *,
    statute_id: str,
    title: str = "",
    source_ref: str = "",
    unbound_marginal_notes: Optional[List[Any]] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> IRStatute:
    """Wrap a PDF-derived body ``IRNode`` into a replay-admissible ``IRStatute``.

    ``body`` is the tree from :func:`pdf_grammar.uk_layout_to_ir` (or
    :func:`pdf_grammar.pdf_text_to_uk_ir`).  Schedules are lifted into
    ``supplements`` so the resulting statute matches the XML loader's shape and
    can be handed directly to :func:`replay_executor.replay_uk_ops` /
    ``UKReplayExecutor``.

    ``unbound_marginal_notes`` (from ``uk_layout_to_ir``) is recorded verbatim in
    metadata as a typed residual — genuine PDF lossiness (a side-note that bound
    to no section), never silently dropped.
    """
    if body.kind is not IRNodeKind.BODY:
        # Normalize any root into a BODY so the executor's body invariants hold.
        body = IRNode(kind=IRNodeKind.BODY, children=(body,))

    new_body, schedules = _split_body_and_schedules(body)

    metadata: dict[str, Any] = {
        "source_lane": "pdf",
        "version_label": "enacted",
    }
    if source_ref:
        metadata["source_ref"] = source_ref
    if unbound_marginal_notes:
        metadata["unbound_marginal_notes"] = tuple(
            {"text": n.text, "page": n.page_num, "y": n.y_top}
            for n in unbound_marginal_notes
        )
    if extra_metadata:
        metadata.update(extra_metadata)

    return IRStatute(
        statute_id=statute_id,
        title=title,
        body=new_body,
        supplements=schedules,
        metadata=metadata,
    )


def uk_pdf_layout_to_replay_base(
    layout: Any,
    *,
    statute_id: str,
    title: str = "",
    source_ref: str = "",
    extra_metadata: Optional[dict[str, Any]] = None,
) -> IRStatute:
    """End-to-end: a segmented :class:`pdf_layout_uk.UKPdfLayout` → replay base.

    Convenience wrapper that runs the marginal-note-binding grammar
    (:func:`pdf_grammar.uk_layout_to_ir`) and then admits the result as a replay
    base, threading the unbound-note residual into metadata.
    """
    from lawvm.uk_legislation.pdf_grammar import uk_layout_to_ir

    body, unbound = uk_layout_to_ir(layout, source_ref=source_ref or statute_id)
    return pdf_ir_to_replay_base(
        body,
        statute_id=statute_id,
        title=title,
        source_ref=source_ref,
        unbound_marginal_notes=unbound,
        extra_metadata=extra_metadata,
    )
