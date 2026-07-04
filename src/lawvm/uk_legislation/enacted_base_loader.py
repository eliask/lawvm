"""enacted_base_loader.py — one chokepoint that loads a UK Act's enacted replay base.

Every UK replay driver (bench, replay, transition-graph, self-consistency, …)
starts from an *enacted base* :class:`~lawvm.core.ir.IRStatute`: the Act as
originally passed, onto which the compiled amendment operations are replayed to
reach current in-force text.  Historically every driver built that base the same
way — ``archive.get(enacted_url)`` (the enacted CLML XML) →
:func:`uk_grafter.parse_uk_statute_ir_bytes`.

That is correct for the ~24k Acts whose enacted XML carries a real ``<Body>``.
But ~7.8k UK Acts exist upstream **only as PDF**: their enacted (and current) XML
is a metadata stub (``NumberOfProvisions="0"``, no ``<Body>``).  Parsing such a
stub yields an *empty* base (a bare ``BODY`` root, zero provisions), so replaying
the effects feed onto it produces nothing — the PDF text never enters replay.

The PDF analogue of the XML loader already exists
(:func:`pdf_replay_base.uk_pdf_layout_to_replay_base`, fed by
:func:`pdf_layout_uk.segment_uk_pdf_layout`), and the PDF blob is already in the
archive under the ``leg://pdf/`` lane (:func:`pdf_acquire.pdf_lane_locator`).  It
was simply never wired into the drivers' base-load step.

This module is that wiring, and *only* that wiring: given a statute id + the
enacted XML bytes + the archive, it returns the enacted base, preferring the XML
body and falling back to the PDF replay base **only when the XML body is empty**.
For any Act with a real XML body the result is byte-identical to the historical
path — the XML lane is unchanged; this is purely additive for the PDF lane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ir import IRNode, IRStatute


# ---------------------------------------------------------------------------
# Empty-base detection
# ---------------------------------------------------------------------------


def _count_provision_nodes(body: IRNode) -> int:
    """Count nodes strictly *below* the BODY root (i.e. real provisions).

    A metadata-only stub parses to a lone ``BODY`` root with no children, so the
    provision count is 0.  A real Act has sections/parts/schedules beneath it.
    The BODY root itself is not counted.
    """
    return sum(1 + _count_provision_nodes(child) for child in body.children)


def xml_base_is_empty(base_ir: IRStatute) -> bool:
    """True iff the XML-parsed base carries no provisions in body *or* supplements.

    This is the exact trigger for the PDF fallback: an enacted XML that is a
    metadata stub (``NumberOfProvisions="0"``) parses to an empty base, and only
    then is the PDF replay base substituted.  An Act with any body provision or
    any schedule supplement is a real XML base and is never overridden.
    """
    if _count_provision_nodes(base_ir.body) > 0:
        return False
    return not any(_count_provision_nodes(s) >= 0 and s.children for s in base_ir.supplements)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnactedBaseResult:
    """The loaded enacted base plus the provenance of where it came from.

    ``source_lane`` is ``"xml"`` (the enacted CLML body was used) or ``"pdf"``
    (the XML was an empty stub and the PDF replay base was substituted).
    ``pdf_status`` is set only on the PDF lane and records why the PDF path
    succeeded or failed (e.g. ``"ok"``, ``"pdf_blob_absent"``,
    ``"pdf_layout_unextractable"`` for image-only scans).
    """

    base_ir: IRStatute
    source_lane: str
    pdf_status: Optional[str] = None


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------


def load_enacted_base(
    statute_id: str,
    enacted_bytes: bytes,
    archive: Any,
    *,
    version_label: str = "enacted",
    pit_date: Optional[str] = None,
    source_path: str = "<archive>",
) -> EnactedBaseResult:
    """Load the enacted replay base for *statute_id*, PDF-substituting empty stubs.

    Parses the enacted CLML ``enacted_bytes`` to an :class:`IRStatute` exactly as
    the historical path did.  If that base carries any provision it is returned
    unchanged (``source_lane="xml"``) — byte-identical to the prior behaviour.

    If (and only if) the XML base is empty (a ``NumberOfProvisions="0"`` stub) the
    PDF lane is consulted: the Act's inline-named PDF blob (already in the archive
    under ``leg://pdf/``) is segmented and admitted as a replay base via the same
    :func:`pdf_replay_base.uk_pdf_layout_to_replay_base` used by the golden
    equivalence test.  On any PDF failure (blob absent, image-only scan that
    pdfplumber cannot extract) the empty XML base is returned with
    ``source_lane="pdf"`` and a ``pdf_status`` naming the failure, so the caller
    can attribute the miss rather than silently proceeding on an empty base.
    """
    # Imported lazily so the XML-only replay path never imports pdfplumber-dependent
    # modules (they are an optional extra); the import only happens on empty stubs.
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    base_ir = parse_uk_statute_ir_bytes(
        enacted_bytes,
        statute_id=statute_id,
        version_label=version_label,
        pit_date=pit_date,
        source_path=source_path,
    )
    if not xml_base_is_empty(base_ir):
        return EnactedBaseResult(base_ir=base_ir, source_lane="xml")

    pdf_base, pdf_status = _load_pdf_base(statute_id, enacted_bytes, archive)
    if pdf_base is not None:
        return EnactedBaseResult(base_ir=pdf_base, source_lane="pdf", pdf_status="ok")
    return EnactedBaseResult(base_ir=base_ir, source_lane="pdf", pdf_status=pdf_status)


def _load_pdf_base(
    statute_id: str,
    enacted_bytes: bytes,
    archive: Any,
) -> tuple[Optional[IRStatute], str]:
    """Try to build a PDF replay base for *statute_id*; return ``(base, status)``.

    ``base`` is None on any failure, with ``status`` naming the class:
    ``"no_pdf_url_in_stub"`` (the stub names no PDF), ``"pdf_blob_absent"`` (the
    PDF was never acquired into the lane), ``"pdf_layout_unextractable"`` (an
    image-only scan pdfplumber cannot read, or pdfplumber is not installed).
    """
    from lawvm.uk_legislation.pdf_acquire import (
        extract_pdf_url_from_stub,
        pdf_lane_locator,
    )

    alt = extract_pdf_url_from_stub(enacted_bytes)
    if alt is None:
        return None, "no_pdf_url_in_stub"

    locator = pdf_lane_locator(alt.url)
    get = getattr(archive, "get", None)
    pdf_bytes = get(locator) if callable(get) else None
    if not pdf_bytes:
        return None, "pdf_blob_absent"

    from lawvm.uk_legislation.pdf_layout_uk import segment_uk_pdf_layout
    from lawvm.uk_legislation.pdf_replay_base import uk_pdf_layout_to_replay_base

    layout = segment_uk_pdf_layout(pdf_bytes)
    if layout is None:
        return None, "pdf_layout_unextractable"

    base = uk_pdf_layout_to_replay_base(
        layout,
        statute_id=statute_id,
        source_ref=statute_id,
        extra_metadata={"pdf_lane_locator": locator},
    )
    if xml_base_is_empty(base):
        # The PDF opened but segmented to no provisions (e.g. a scan whose only
        # extractable words were furniture). Treat as unextractable, not a base.
        return None, "pdf_layout_unextractable"
    return base, "ok"
