"""Level-1 page-level orchestration — metadata, convergence, faithful simulacra.

Track B of the two-level PDF→IR pipeline (``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``
§1, §3, §5 Decisions 1/2/7/9/10). Turns a page's cold ``propose_page_struct`` read
into a faithful, immutable ``PageSimulacrum``:

* **Metadata capture** (Decision 7): each node gets deterministic ``NodeMetadata``
  (geometry band/indent/y-order from ``PageLine``, continuation cues + content
  hints as pure string fns) encoded onto ``attrs`` — an AFFORDANCE Level 2 uses,
  never authority shown to the model.
* **Recurrence pre-pass** (§4): a whole-doc cross-page band-recurrence map written
  as ``rec.band_count`` — a running-header / page-number furniture affordance.
* **Furniture kept** (§1 reversal / §4): a bare page number / recurring header is
  TAGGED ``hint.furniture`` and KEPT as a node — "this is furniture" is a
  cross-page judgment, so the DROP is Level 2's call, never Level 1's.
* **Convergence** (Decisions 2/10): round 1 cold; refine rounds ONLY if the closed
  gate fires; each round patches the model's OWN rendered reconstruction against
  the page image; terminate on empty-patch / fixpoint (SHA over the resolved
  tree) / oscillation / max_iters=4 / truncation.
* **Faithfulness** (Decision 1): ``_page_assurance`` runs before the gate; freeform
  regions are EXCLUDED from text-witness corroboration; the ``unwitnessed_content``
  tripwire caps a governed node whose text is in NEITHER the reading-order witness
  NOR a freeform region at ``UNADJUDICATED_PROPOSAL``.

The orchestration is EXPOSED (``converge_page`` / ``build_page_simulacra``); the
central integration step (Track C merge) wires ``struct_document_ingest`` to call
it — this module does NOT rewire the existing compose orchestration (scope guard).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.adjudication import Adjudicator
from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.metadata import NodeMetadata, encode_metadata
from lawvm.ingest.page_elements import (
    PageElements,
    PageLine,
    line_ends_terminal,
    line_has_hyphen_tail,
    line_has_section_ref,
    line_is_bare_page_number,
    line_is_caps,
    line_is_numeric_heavy,
    line_list_marker,
    line_section_number,
    line_starts_lower,
)
from lawvm.ingest.simulacrum import (
    ConvergenceInfo,
    FreeformRegion,
    PageSimulacrum,
)
from lawvm.ingest.struct_wire import (
    StructBuildNode,
    _apply_patches,
    _parse_command_line,
    _struct_units,
)

# The Level-1 producer id stamped on ``prov.producer``.
_PRODUCER_ID = "vision_struct.v1"

# Convergence loop bound (Decision 10). max_iters counts REFINE rounds after the
# cold round-1 read.
MAX_CONVERGE_ITERS = 4

# Terminator-compliance floor below which the gate fires (Decision 2).
_TERMINATOR_COMPLIANCE_FLOOR = 0.98


# --------------------------------------------------------------------------- #
# Metadata capture (Decision 7) — geometry + cues + hints, deterministic.      #
# --------------------------------------------------------------------------- #


def _metadata_for_text(
    text: str,
    *,
    line: Optional[PageLine],
    y_order: Optional[int],
    band_count: Optional[int],
    furniture: bool,
    freeform_reason: Optional[str],
    converged: bool,
) -> NodeMetadata:
    """Deterministic ``NodeMetadata`` for one node — geometry + string cues + hints.

    Geometry (band/indent/col/y-order) comes from the matched ``PageLine``; the
    continuation cues + content hints are PURE string functions of ``text``. All
    affordances, never authority.
    """
    band = line.band if line is not None else None
    indent = line.indent if line is not None else None
    col = line.col if line is not None else None
    stripped = text.strip()
    return NodeMetadata(
        band=band,
        col=col,
        indent=indent,
        y_order=y_order,
        caps=line_is_caps(stripped),
        ends_terminal=line_ends_terminal(stripped),
        starts_lower=line_starts_lower(stripped),
        hyphen_tail=line_has_hyphen_tail(stripped),
        list_marker=line_list_marker(stripped),
        section_number=line_section_number(stripped),
        band_count=band_count,
        numeric=line_is_numeric_heavy(stripped),
        section_ref=line_has_section_ref(stripped),
        furniture=furniture,
        freeform_reason=freeform_reason,
        producer=_PRODUCER_ID,
        converged=converged,
    )


def _line_index_by_text(page_lines: Sequence[PageLine]) -> Dict[str, PageLine]:
    """Map a page line's normalized text → its ``PageLine`` (first wins).

    Node text is span-copied from the reading-order lines, so a node's leading
    physical line matches a ``PageLine`` by text — the deterministic bridge from
    the geometry-free node text to its per-line geometry.
    """
    out: Dict[str, PageLine] = {}
    for pl in page_lines:
        key = " ".join(pl.text.split())
        if key and key not in out:
            out[key] = pl
    return out


# --------------------------------------------------------------------------- #
# Recurrence pre-pass (§4) — cross-page band recurrence, computed once.        #
# --------------------------------------------------------------------------- #


def band_recurrence_map(pages: Sequence[PageElements]) -> Dict[str, int]:
    """Whole-doc cross-page recurrence of a ``(band, normalized-text)`` line (§4).

    A line's text recurring at the SAME margin band across pages is a running
    header / footer / page-number FURNITURE affordance (``rec.band_count``). Bare
    page numbers are counted by band alone (the digit varies per page) so a
    ``12 / 13 / 14`` footer still recurs. Computed ONCE as shared context; the
    model confirms furniture across pages, never obeys the count.
    """
    counts: Dict[str, int] = {}
    for pe in pages:
        seen_on_page: set = set()
        for pl in pe.page_lines:
            if pl.band is None:
                continue
            if line_is_bare_page_number(pl.text):
                key = f"{pl.band}\x00#pageno"
            else:
                key = f"{pl.band}\x00{' '.join(pl.text.split())}"
            if key in seen_on_page:
                continue
            seen_on_page.add(key)
            counts[key] = counts.get(key, 0) + 1
    return counts


def _recurrence_key(text: str, line: Optional[PageLine]) -> Optional[str]:
    if line is None or line.band is None:
        return None
    if line_is_bare_page_number(line.text):
        return f"{line.band}\x00#pageno"
    return f"{line.band}\x00{' '.join(text.split())}"


# --------------------------------------------------------------------------- #
# Furniture hint (§1/§4) — bare page number OR recurring header, KEPT.         #
# --------------------------------------------------------------------------- #


def _is_furniture_candidate(
    text: str, line: Optional[PageLine], band_count: Optional[int], page_count: int
) -> bool:
    """Is this node a likely-furniture candidate (KEPT, tagged ``hint.furniture``)?

    Two deterministic affordances: a bare page number in a margin band, OR a line
    recurring at the same band on >= half the document's pages (>=2). A pure
    candidate — Level 2 confirms and DROPS; Level 1 only tags + keeps.
    """
    if line is None or line.band is None or line.band == "body":
        return False
    if line_is_bare_page_number(line.text):
        return True
    if band_count is not None and band_count >= 2 and band_count * 2 >= page_count:
        return True
    return False


# --------------------------------------------------------------------------- #
# Convergence (Decisions 2 / 10) — gate + patch-to-fixpoint refine loop.       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ConvergedPage:
    """Result of ``converge_page``: the resolved forest + convergence + freeform + assurance."""

    nodes: Tuple[StructBuildNode, ...]
    convergence: ConvergenceInfo
    freeform: Tuple[FreeformRegion, ...]
    assurance: AssuranceTier
    raw_wire_digests: Tuple[str, ...]


def _resolved_tree_hash(nodes: Sequence[StructBuildNode]) -> str:
    """SHA-256 over the canonical RESOLVED forest (Decision 10 fixpoint key).

    Hashes kind + text + freeform + image-digest + child structure — NOT the raw
    wire (two wires that resolve to the same tree are the same fixpoint)."""
    h = hashlib.sha256()

    def _walk(n: StructBuildNode) -> None:
        h.update(n.kind.value.encode("utf-8"))
        h.update(b"\x00")
        h.update(n.text.encode("utf-8"))
        h.update(b"\x00")
        if n.freeform is not None:
            h.update(f"{n.freeform.bbox}|{n.freeform.reason}".encode("utf-8"))
        if n.image is not None:
            h.update(n.image.digest.encode("utf-8"))
        h.update(b"\x01")  # open children
        for c in n.children:
            _walk(c)
        h.update(b"\x02")  # close children

    for n in nodes:
        _walk(n)
    return h.hexdigest()


def _text_leaf_paths(nodes: Sequence[StructBuildNode]) -> List[Tuple[int, ...]]:
    """Pre-order paths of the text-bearing leaves — the PATCH address space.

    Mirrors ``render_simulacrum_as_numbered_lines``: one entry per text-bearing
    node in pre-order. The refine round's numbered lines are 1:1 with these paths,
    so a PATCH on line N rewrites the node at path[N-1]."""
    paths: List[Tuple[int, ...]] = []

    def _walk(n: StructBuildNode, path: Tuple[int, ...]) -> None:
        if n.text.strip():
            paths.append(path)
        for i, c in enumerate(n.children):
            _walk(c, path + (i,))

    for i, n in enumerate(nodes):
        _walk(n, (i,))
    return paths


def _node_at(nodes: Sequence[StructBuildNode], path: Tuple[int, ...]) -> StructBuildNode:
    node = nodes[path[0]]
    for idx in path[1:]:
        node = node.children[idx]
    return node


def _rewrite_text_at(
    nodes: Tuple[StructBuildNode, ...],
    replacements: Mapping[Tuple[int, ...], str],
) -> Tuple[StructBuildNode, ...]:
    """Return a new forest with text-leaf substitutions applied (text-PATCH only).

    Structural PATCH is milestone 2 (Decision 1), so only ``.text`` changes — the
    tree SHAPE is invariant across refine rounds, which bounds the oscillation
    axis to text alone."""

    def _walk(n: StructBuildNode, path: Tuple[int, ...]) -> StructBuildNode:
        new_text = replacements.get(path, n.text)
        children = tuple(_walk(c, path + (i,)) for i, c in enumerate(n.children))
        if new_text == n.text and children == n.children:
            return n
        return StructBuildNode(
            kind=n.kind, text=new_text, image=n.image, freeform=n.freeform, children=children
        )

    return tuple(_walk(n, (i,)) for i, n in enumerate(nodes))


def _current_numbered_lines(nodes: Sequence[StructBuildNode]) -> List[str]:
    """The text-leaf lines (one per text-bearing node, pre-order) — PATCH targets."""
    return [
        " ".join(_node_at(nodes, p).text.split("\n"))
        for p in _text_leaf_paths(nodes)
    ]


def _apply_delta_wire(
    nodes: Tuple[StructBuildNode, ...], delta_wire: str
) -> Tuple[Tuple[StructBuildNode, ...], int]:
    """Parse a refine round's PATCH-delta wire and apply it → (new forest, patch count).

    The delta wire's PATCH commands address the CURRENT numbered lines (1:1 with
    the text-leaf pre-order). Reuses ``_apply_patches`` verbatim (Decision 1's
    single-line invariant), then maps the patched lines back onto the leaves."""
    paths = _text_leaf_paths(nodes)
    lines = _current_numbered_lines(nodes)
    patch_cmds = []
    for unit, _terminated in _struct_units(delta_wire):
        cmd = _parse_command_line(unit)
        if cmd is not None and cmd.kind_token.upper() == "PATCH":
            patch_cmds.append(cmd)
    patched, _findings, count = _apply_patches(lines, patch_cmds)
    if count == 0:
        return nodes, 0
    replacements = {
        paths[i]: patched[i]
        for i in range(min(len(paths), len(patched)))
        if patched[i] != lines[i]
    }
    return _rewrite_text_at(nodes, replacements), count


def _freeform_index(nodes: Sequence[StructBuildNode]) -> Tuple[FreeformRegion, ...]:
    """Index every MATH / VERBATIM region in the resolved forest as a ``FreeformRegion``."""
    out: List[FreeformRegion] = []

    def _walk(n: StructBuildNode, path: Tuple[int, ...]) -> None:
        if n.freeform is not None and n.kind in (
            SourceDocumentNodeKind.MATH_REGION,
            SourceDocumentNodeKind.VERBATIM_REGION,
        ):
            x0, y0, x1, y1 = n.freeform.bbox
            out.append(
                FreeformRegion(
                    node_path=path,
                    kind="math" if n.kind is SourceDocumentNodeKind.MATH_REGION else "verbatim",
                    reason=n.freeform.reason,
                    bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                )
            )
        for i, c in enumerate(n.children):
            _walk(c, path + (i,))

    for i, n in enumerate(nodes):
        _walk(n, (i,))
    return tuple(out)


def _struct_text_of(node: StructBuildNode) -> str:
    parts = [node.text] if node.text else []
    for c in node.children:
        t = _struct_text_of(c)
        if t:
            parts.append(t)
    return "\n".join(parts)


def _gate_reasons(
    build,
    assurance: AssuranceTier,
    freeform: Sequence[FreeformRegion],
    reading_order_text: str,
) -> Tuple[str, ...]:
    """The Decision-2 closed trigger set fired on the cold round-1 artifacts.

    Clean pages fire NOTHING → single-pass. Any of: >=1 freeform region; any
    build findings; patches_applied>0; terminator compliance <0.98; SINGLE_WITNESS
    DESPITE a non-empty reading-order witness; (truncation is handled by the
    caller, which passes a ``truncated`` reason)."""
    reasons: List[str] = []
    if freeform:
        reasons.append("freeform_region")
    if build.findings:
        reasons.append("findings")
    if build.patches_applied > 0:
        reasons.append("patches_applied")
    total = build.total_command_lines
    if total and (build.terminated_command_lines / total) < _TERMINATOR_COMPLIANCE_FLOOR:
        reasons.append("terminator_below_floor")
    if assurance is AssuranceTier.SINGLE_WITNESS and reading_order_text.strip():
        reasons.append("single_witness_with_witness")
    return tuple(reasons)


def converge_page(
    vision,
    manifestation,
    page_num: int,
    page_elements: PageElements,
    *,
    reading_order_text: str = "",
    adjudicator: Optional[Adjudicator] = None,
    leaf_mode: str = "patch",
    max_iters: int = MAX_CONVERGE_ITERS,
) -> ConvergedPage:
    """Patch-to-convergence for one page (Decisions 2 / 10).

    Round 1 is a COLD ``propose_page_struct`` read. ``_page_assurance`` runs, then
    the closed Decision-2 gate: if NOTHING fires the page stays single-pass
    (``gated_single_pass``). Otherwise refine rounds render the model's OWN
    resolved reconstruction back as numbered lines + the page image → a PATCH
    delta; terminate on empty-patch / fixpoint (SHA over the resolved tree) /
    oscillation (an earlier-round hash re-entry → keep last, flag) / max_iters /
    truncation. Structural PATCH is milestone 2 — text-delta only."""
    from lawvm.ingest.adjudicated_ingest import _page_assurance
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerTruncated,
        render_simulacrum_as_numbered_lines,
    )

    region = SourceAnchor(
        artifact_digest=manifestation.artifact_digest,
        locator=f"page={page_num}",
        page_num=page_num,
    )
    result = vision.propose_page_struct(
        manifestation, page_num, page_elements, leaf_mode=leaf_mode
    )
    build = result.build
    nodes: Tuple[StructBuildNode, ...] = build.roots
    raw_digests: List[str] = [hashlib.sha256(result.raw_content.encode("utf-8")).hexdigest()]
    freeform = _freeform_index(nodes)
    reconstructed = "\n".join(_struct_text_of(n) for n in nodes)
    assurance = _page_assurance(reconstructed, reading_order_text, adjudicator, region)

    gate_reasons = _gate_reasons(build, assurance, freeform, reading_order_text)
    round_hashes: List[str] = [_resolved_tree_hash(nodes)]
    patches_total = build.patches_applied

    if not gate_reasons:
        return ConvergedPage(
            nodes=nodes,
            convergence=ConvergenceInfo(
                rounds=1,
                round_hashes=tuple(round_hashes),
                termination="gated_single_pass",
                gate_reasons=(),
                patches_total=patches_total,
            ),
            freeform=freeform,
            assurance=assurance,
            raw_wire_digests=tuple(raw_digests),
        )

    termination = "max_iters"
    rounds = 1
    for _ in range(max_iters):
        numbered = render_simulacrum_as_numbered_lines(nodes)
        if not numbered.strip():
            termination = "empty_patch"
            break
        try:
            delta_wire = vision.propose_page_patch_delta(manifestation, page_num, numbered)
        except VisionProducerTruncated:
            termination = "truncated"
            break
        raw_digests.append(hashlib.sha256(delta_wire.encode("utf-8")).hexdigest())
        new_nodes, count = _apply_delta_wire(nodes, delta_wire)
        rounds += 1
        patches_total += count
        if count == 0:
            termination = "empty_patch"
            break
        new_hash = _resolved_tree_hash(new_nodes)
        if new_hash == round_hashes[-1]:
            nodes = new_nodes
            round_hashes.append(new_hash)
            termination = "fixpoint"
            break
        if new_hash in round_hashes:
            # An earlier-round tree re-entry — oscillation. Keep the LAST result,
            # flag it, no tier effect (Decision 10).
            nodes = new_nodes
            round_hashes.append(new_hash)
            termination = "oscillation"
            break
        nodes = new_nodes
        round_hashes.append(new_hash)
    else:
        termination = "max_iters"

    freeform = _freeform_index(nodes)
    return ConvergedPage(
        nodes=nodes,
        convergence=ConvergenceInfo(
            rounds=rounds,
            round_hashes=tuple(round_hashes),
            termination=termination,
            gate_reasons=gate_reasons,
            patches_total=patches_total,
        ),
        freeform=freeform,
        assurance=assurance,
        raw_wire_digests=tuple(raw_digests),
    )


# --------------------------------------------------------------------------- #
# Faithfulness tripwire (Decision 1) — unwitnessed_content cap.                #
# --------------------------------------------------------------------------- #


def _witness_words(reading_order_text: str) -> set:
    """Normalized word multiset (as a set) of the independent reading-order witness."""
    return {w for w in reading_order_text.lower().split() if w}


def _is_witnessed(text: str, witness_words: set) -> bool:
    """Is a node's text corroborated by the reading-order witness (word containment)?

    A governed node whose words are ALL present in the reading-order witness is
    corroborated. A node with NO text (pure container / image) is vacuously
    witnessed (nothing to corroborate)."""
    words = [w for w in text.lower().split() if w]
    if not words:
        return True
    return all(w in witness_words for w in words)


# --------------------------------------------------------------------------- #
# Simulacrum producer (§1 interface out) — StructBuildNode forest → PageSimulacrum. #
# --------------------------------------------------------------------------- #


def _lower_with_metadata(
    node: StructBuildNode,
    *,
    region: SourceAnchor,
    digest: str,
    page_num: int,
    tier: AssuranceTier,
    line_index: Mapping[str, PageLine],
    recurrence: Mapping[str, int],
    page_count: int,
    witness_words: set,
    converged: bool,
    y_counter: List[int],
) -> SourceDocumentNode:
    """Lower one ``StructBuildNode`` subtree → a metadata-annotated ``SourceDocumentNode``.

    Attaches deterministic ``NodeMetadata`` (geometry from the matched ``PageLine``,
    string cues/hints, recurrence, furniture hint) and applies the
    ``unwitnessed_content`` tripwire: a governed node whose text is in NEITHER the
    reading-order witness NOR a freeform region is capped at
    ``UNADJUDICATED_PROPOSAL`` (Decision 1). Freeform regions are EXCLUDED from the
    witness check (default their page tier)."""
    is_freeform = node.kind in (
        SourceDocumentNodeKind.MATH_REGION,
        SourceDocumentNodeKind.VERBATIM_REGION,
    )
    is_image = node.kind is SourceDocumentNodeKind.IMAGE_REGION

    y_order = y_counter[0]
    y_counter[0] += 1

    # Match the node's leading physical line to a PageLine for geometry.
    first_line = node.text.split("\n", 1)[0].strip() if node.text else ""
    key = " ".join(first_line.split())
    page_line = line_index.get(key)
    band_count = None
    if page_line is not None:
        rk = _recurrence_key(node.text, page_line)
        if rk is not None:
            band_count = recurrence.get(rk)

    furniture = (
        not is_freeform
        and not is_image
        and _is_furniture_candidate(node.text, page_line, band_count, page_count)
    )
    freeform_reason = node.freeform.reason if (is_freeform and node.freeform) else None

    # Tripwire (Decision 1): a text-bearing governed node that is NOT freeform and
    # NOT image, whose words are in neither the witness nor a freeform region, is
    # capped at UNADJUDICATED_PROPOSAL. Freeform / image / furniture-page-number
    # text is EXCLUDED from the witness requirement.
    node_tier = tier
    if (
        node.text.strip()
        and not is_freeform
        and not is_image
        and not furniture
        and not _is_witnessed(node.text, witness_words)
    ):
        node_tier = AssuranceTier.UNADJUDICATED_PROPOSAL

    meta = _metadata_for_text(
        node.text,
        line=page_line,
        y_order=y_order,
        band_count=band_count,
        furniture=furniture,
        freeform_reason=freeform_reason,
        converged=converged,
    )
    attrs: Dict[str, str] = dict(encode_metadata(meta))

    anchor = region
    if is_image and node.image is not None:
        x0, y0, x1, y1 = node.image.bbox
        anchor = SourceAnchor(
            artifact_digest=digest,
            locator=f"page={page_num};bbox={x0},{y0},{x1},{y1}",
            page_num=page_num,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        )
        attrs.update(
            {
                "image_digest": node.image.digest,
                "image_index": str(node.image.index),
                "media_type": node.image.media_type,
                "px_width": str(node.image.width),
                "px_height": str(node.image.height),
                "role": node.image.role,
            }
        )
    elif is_freeform and node.freeform is not None:
        x0, y0, x1, y1 = node.freeform.bbox
        anchor = SourceAnchor(
            artifact_digest=digest,
            locator=f"page={page_num};bbox={x0},{y0},{x1},{y1}",
            page_num=page_num,
            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        )
    elif page_line is not None and page_line.bbox is not None:
        b = page_line.bbox
        anchor = SourceAnchor(
            artifact_digest=digest,
            locator=f"page={page_num};bbox={b.x0},{b.y0},{b.x1},{b.y1}",
            page_num=page_num,
            bbox=b,
        )

    children = tuple(
        _lower_with_metadata(
            c,
            region=region,
            digest=digest,
            page_num=page_num,
            tier=tier,
            line_index=line_index,
            recurrence=recurrence,
            page_count=page_count,
            witness_words=witness_words,
            converged=converged,
            y_counter=y_counter,
        )
        for c in node.children
    )
    return SourceDocumentNode(
        kind=node.kind,
        assurance_tier=node_tier,
        anchor=anchor,
        text=node.text,
        children=children,
        attrs=attrs,
    )


def build_page_simulacrum(
    converged: ConvergedPage,
    manifestation,
    page_num: int,
    page_elements: PageElements,
    *,
    reading_order_text: str = "",
    recurrence: Optional[Mapping[str, int]] = None,
    page_count: int = 1,
) -> PageSimulacrum:
    """Produce the immutable ``PageSimulacrum`` evidence record for one converged page.

    Lowers the converged forest to metadata-annotated ``SourceDocumentNode``s
    (geometry + cues + hints + recurrence + furniture hint + convergence flag),
    applies the ``unwitnessed_content`` tripwire, and re-indexes the freeform
    regions against the FINAL tree (Decision 10: ledger addresses only the final
    simulacrum). The ``FreeformRegion.node_path`` matches the lowered tree paths."""
    digest = manifestation.artifact_digest
    region = SourceAnchor(
        artifact_digest=digest, locator=f"page={page_num}", page_num=page_num
    )
    line_index = _line_index_by_text(page_elements.page_lines)
    rec = recurrence if recurrence is not None else {}
    witness_words = _witness_words(reading_order_text)
    converged_flag = converged.convergence.termination in ("empty_patch", "fixpoint")

    y_counter = [0]
    nodes = tuple(
        _lower_with_metadata(
            n,
            region=region,
            digest=digest,
            page_num=page_num,
            tier=converged.assurance,
            line_index=line_index,
            recurrence=rec,
            page_count=page_count,
            witness_words=witness_words,
            converged=converged_flag,
            y_counter=y_counter,
        )
        for n in converged.nodes
    )
    return PageSimulacrum(
        page_num=page_num,
        nodes=nodes,
        freeform=converged.freeform,
        convergence=converged.convergence,
        assurance=converged.assurance,
        raw_wire_digests=converged.raw_wire_digests,
    )


# --------------------------------------------------------------------------- #
# PageSimulacrum ↔ JSON codec (Decision 11 persistence — round-trippable).      #
# --------------------------------------------------------------------------- #


def _bbox_to_json(bbox: Optional[BBox]) -> Optional[list]:
    return None if bbox is None else [bbox.x0, bbox.y0, bbox.x1, bbox.y1]


def _bbox_from_json(raw) -> Optional[BBox]:
    if raw is None:
        return None
    x0, y0, x1, y1 = raw
    return BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1))


def _anchor_to_json(anchor: SourceAnchor) -> dict:
    return {
        "artifact_digest": anchor.artifact_digest,
        "locator": anchor.locator,
        "page_num": anchor.page_num,
        "bbox": _bbox_to_json(anchor.bbox),
        "byte_range": list(anchor.byte_range) if anchor.byte_range is not None else None,
    }


def _anchor_from_json(raw: dict) -> SourceAnchor:
    br = raw.get("byte_range")
    return SourceAnchor(
        artifact_digest=raw["artifact_digest"],
        locator=raw["locator"],
        page_num=raw.get("page_num"),
        bbox=_bbox_from_json(raw.get("bbox")),
        byte_range=(int(br[0]), int(br[1])) if br is not None else None,
    )


def _node_to_json(node: SourceDocumentNode) -> dict:
    return {
        "kind": node.kind.value,
        "assurance_tier": node.assurance_tier.value,
        "anchor": _anchor_to_json(node.anchor),
        "label": node.label,
        "text": node.text,
        "attrs": dict(node.attrs),
        "children": [_node_to_json(c) for c in node.children],
    }


def _node_from_json(raw: dict) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=SourceDocumentNodeKind(raw["kind"]),
        assurance_tier=AssuranceTier(raw["assurance_tier"]),
        anchor=_anchor_from_json(raw["anchor"]),
        label=raw.get("label"),
        text=raw.get("text", ""),
        children=tuple(_node_from_json(c) for c in raw.get("children", ())),
        attrs=dict(raw.get("attrs", {})),
    )


def page_simulacrum_to_json(sim: PageSimulacrum) -> dict:
    """Serialize a ``PageSimulacrum`` to a JSON-able dict (round-trips exactly)."""
    return {
        "page_num": sim.page_num,
        "nodes": [_node_to_json(n) for n in sim.nodes],
        "freeform": [
            {
                "node_path": list(f.node_path),
                "kind": f.kind,
                "reason": f.reason,
                "bbox": _bbox_to_json(f.bbox),
            }
            for f in sim.freeform
        ],
        "convergence": {
            "rounds": sim.convergence.rounds,
            "round_hashes": list(sim.convergence.round_hashes),
            "termination": sim.convergence.termination,
            "gate_reasons": list(sim.convergence.gate_reasons),
            "patches_total": sim.convergence.patches_total,
        },
        "assurance": sim.assurance.value,
        "raw_wire_digests": list(sim.raw_wire_digests),
    }


def page_simulacrum_from_json(raw: dict) -> PageSimulacrum:
    """Reconstruct a ``PageSimulacrum`` from ``page_simulacrum_to_json`` output."""
    conv = raw["convergence"]
    return PageSimulacrum(
        page_num=raw["page_num"],
        nodes=tuple(_node_from_json(n) for n in raw.get("nodes", ())),
        freeform=tuple(
            FreeformRegion(
                node_path=tuple(f["node_path"]),
                kind=f["kind"],
                reason=f["reason"],
                bbox=_bbox_from_json(f.get("bbox")),
            )
            for f in raw.get("freeform", ())
        ),
        convergence=ConvergenceInfo(
            rounds=conv["rounds"],
            round_hashes=tuple(conv["round_hashes"]),
            termination=conv["termination"],
            gate_reasons=tuple(conv["gate_reasons"]),
            patches_total=conv["patches_total"],
        ),
        assurance=AssuranceTier(raw["assurance"]),
        raw_wire_digests=tuple(raw.get("raw_wire_digests", ())),
    )


def build_page_simulacra(
    vision,
    manifestation,
    page_element_producer,
    reading_order_pages: Sequence[str],
    *,
    adjudicator: Optional[Adjudicator] = None,
    leaf_mode: str = "patch",
    max_iters: int = MAX_CONVERGE_ITERS,
    max_pages: int = 5000,
) -> Tuple[PageSimulacrum, ...]:
    """Produce the ``Sequence[PageSimulacrum]`` for a manifestation (§1 interface out).

    Runs the recurrence pre-pass over ALL pages first (whole-doc furniture
    affordance, §4), then per page: ``converge_page`` (gate + patch-to-fixpoint) →
    ``build_page_simulacrum`` (metadata + tripwire). The result is the Level-1 →
    Level-2 bridge; persist it via ``ParsedIrStore.put_page_simulacrum`` so
    re-running Level 2 never re-runs the model (Decision 11)."""
    page_count = min(len(reading_order_pages), max_pages)
    all_elements: List[PageElements] = [
        page_element_producer.page_elements(manifestation.source_bytes, i + 1)
        for i in range(page_count)
    ]
    recurrence = band_recurrence_map(all_elements)
    out: List[PageSimulacrum] = []
    for idx in range(page_count):
        page_num = idx + 1
        pe = all_elements[idx]
        ro_text = reading_order_pages[idx]
        converged = converge_page(
            vision,
            manifestation,
            page_num,
            pe,
            reading_order_text=ro_text,
            adjudicator=adjudicator,
            leaf_mode=leaf_mode,
            max_iters=max_iters,
        )
        out.append(
            build_page_simulacrum(
                converged,
                manifestation,
                page_num,
                pe,
                reading_order_text=ro_text,
                recurrence=recurrence,
                page_count=page_count,
            )
        )
    return tuple(out)
