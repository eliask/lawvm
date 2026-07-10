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
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.source_document.adjudication import Adjudicator
from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.llm_backends.token_meter import meter_unit
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
from lawvm.ingest.suspect_region import (
    SuspectRegion,
    cross_reader_disagrees,
    lexical_implausibility,
    more_plausible,
)
from lawvm.ingest.struct_wire import (
    NodePatch,
    StructBuildNode,
    StructBuildResult,
    _apply_patches,
    _parse_command_line,
    _struct_units,
    collect_node_patches,
)

# The Level-1 producer id stamped on ``prov.producer``.
_PRODUCER_ID = "vision_struct.v1"

# Convergence loop bound (Decision 10). max_iters counts REFINE rounds after the
# cold round-1 read.
MAX_CONVERGE_ITERS = 4

# Terminator-compliance floor below which the gate fires (Decision 2).
_TERMINATOR_COMPLIANCE_FLOOR = 0.98

# §8 agentic re-read: the DPI a suspect region is re-rendered + re-read at. The
# recovery lever is ISOLATION, not DPI: cropping the region into its OWN image lets
# its text command a large share of the vision encoder's fixed token grid (a glyph
# lost as a tiny fraction of the whole page is resolved once it fills a crop —
# measured on a scanned gazette, correct even at a 400 px crop). This DPI just sets
# the crop's sharpness once isolated; see ``visual.DEFAULT_REREAD_DPI``.
_REREAD_DPI = 300
# Cap the re-reads per page — output-sparsity guard (a page with a dozen garbles
# is a page-level failure, not a re-read case; the residue stays typed-suspect).
_MAX_REREADS_PER_PAGE = 8

# Default per-PDF Level-1 page concurrency (§ pipeline concurrency): each page's
# ``converge_page`` runs in its own worker so the independent per-page vision HTTP
# calls overlap and keep the GPU fed while ONE PDF is processed. Bounded — the
# vision backend serves a fixed batch of requests, so more workers than that just
# queue. Overridable per-call (``build_page_simulacra(max_workers=…)``) or by env
# (``LAWVM_INGEST_PAGE_CONCURRENCY``). Determinism is INDEPENDENT of this value:
# results are assembled strictly by page index, never by completion order.
_DEFAULT_PAGE_CONCURRENCY = int(
    os.environ.get("LAWVM_INGEST_PAGE_CONCURRENCY", "8") or "8"
)


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
    continuation cues + content hints are PURE string functions of ``text``; the
    ``meta.v2`` typography (font / size_class / bold / italic) rides on the matched
    ``PageLine`` (the pdfplumber char lane aligned to it), OPTIONAL — absent when
    unaligned. All affordances, never authority.
    """
    band = line.band if line is not None else None
    indent = line.indent if line is not None else None
    col = line.col if line is not None else None
    font = line.font if line is not None else None
    size_class = line.size_class if line is not None else None
    bold = line.bold if line is not None else False
    italic = line.italic if line is not None else False
    stripped = text.strip()
    return NodeMetadata(
        band=band,
        col=col,
        indent=indent,
        y_order=y_order,
        caps=line_is_caps(stripped),
        font=font,
        size_class=size_class,
        bold=bold,
        italic=italic,
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
    the geometry-free node text to its per-line geometry. Kept as the SECONDARY
    (fallback) bridge only; the PRIMARY bridge is the source-line index (reading
    order rank) via ``_resolve_page_line`` — see Decision 8.
    """
    out: Dict[str, PageLine] = {}
    for pl in page_lines:
        key = " ".join(pl.text.split())
        if key and key not in out:
            out[key] = pl
    return out


def _resolve_page_line(
    text: str,
    rank: int,
    page_lines: Sequence[PageLine],
    line_index: Mapping[str, PageLine],
) -> Optional[PageLine]:
    """Bridge a text leaf to its ``PageLine`` by SOURCE-LINE INDEX first (Decision 8).

    The cold read enumerates the page's reading-order lines; the Nth text-bearing
    leaf corresponds to the Nth reading-order ``PageLine`` (``page_lines`` is in that
    same order). Binding by that RANK is robust where binding by exact text is NOT:

      * a leaf the converge loop or a §8 re-read CORRECTED no longer matches any
        line by string, yet its rank still locates its geometry (so it stays
        re-readable and keeps its bbox exactly where the page is hardest); and
      * recurring identical lines each bind to their OWN occurrence instead of all
        collapsing onto the first (the exact-text-first-wins bug).

    Text match is retained as a CORROBORATION / fallback: when the rank line's text
    still equals the leaf's leading line we take it (identical result, extra
    confidence); when the rank is out of range (a leaf with no source line — an
    inline/re-transcribed leaf that outran the cold lines) we fall back to the
    text index so such a leaf still binds if its text happens to match a line.
    """
    if 0 <= rank < len(page_lines):
        return page_lines[rank]
    # No source line at this rank (more text leaves than cold lines) → best-effort
    # text bridge, else no geometry (typed absence, never a guessed bbox).
    first_line = text.split("\n", 1)[0].strip() if text else ""
    key = " ".join(first_line.split())
    return line_index.get(key)


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

    Only ``.text`` changes here — the tree SHAPE is invariant. Structural change
    (delete/relabel, milestone 2) is applied SEPARATELY by
    ``_apply_structural_patches`` AFTER this, so the text delta always addresses a
    stable line-index space within one round."""

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


def _apply_structural_patches(
    nodes: Tuple[StructBuildNode, ...], node_patches: Sequence[NodePatch]
) -> Tuple[Tuple[StructBuildNode, ...], int]:
    """Apply node-addressed structural PATCHes to the forest → (new forest, count).

    Milestone-2 structural PATCH in the converge refine loop (§5 Decision 1): the
    address ``N<id>`` is the 1-based text-leaf LINE index (1:1 with the numbered
    lines the model just patched, ``render_simulacrum_as_numbered_lines``). A
    delete drops the addressed node + its whole subtree; a relabel swaps its kind.
    A delete/relabel RENUMBERS the rendered lines the NEXT round patches against —
    the second oscillation axis (Decision 10); the resolved-tree hash already
    covers structural change, so the caller's oscillation guard handles it.

    Deletes are resolved to STABLE node identities (by their pre-op path) before
    any mutation, so multiple ops in one round never trip over each other's
    renumbering; relabels are then applied by path. A line index that doesn't
    resolve is skipped (dropped) here — its finding was already emitted upstream.
    """
    paths = _text_leaf_paths(nodes)
    delete_paths: set = set()
    relabel_by_path: Dict[Tuple[int, ...], SourceDocumentNodeKind] = {}
    applied = 0
    for patch in node_patches:
        idx = patch.node_id - 1  # N<id> is the 1-based text-leaf line index
        if not (0 <= idx < len(paths)):
            continue  # out-of-range line index → dropped (finding upstream)
        path = paths[idx]
        if patch.kind is None:
            delete_paths.add(path)
            applied += 1
        else:
            relabel_by_path[path] = patch.kind
            applied += 1

    def _rebuild(
        n: StructBuildNode, path: Tuple[int, ...]
    ) -> Optional[StructBuildNode]:
        # A deleted node (or one under a deleted ancestor) collapses to None; its
        # whole subtree goes with it.
        if path in delete_paths:
            return None
        children: List[StructBuildNode] = []
        for i, c in enumerate(n.children):
            kid = _rebuild(c, path + (i,))
            if kid is not None:
                children.append(kid)
        kind = relabel_by_path.get(path, n.kind)
        new_children = tuple(children)
        if kind == n.kind and new_children == n.children:
            return n
        return StructBuildNode(
            kind=kind, text=n.text, image=n.image, freeform=n.freeform, children=new_children
        )

    new_roots: List[StructBuildNode] = []
    for i, n in enumerate(nodes):
        kid = _rebuild(n, (i,))
        if kid is not None:
            new_roots.append(kid)
    return tuple(new_roots), applied


def _apply_delta_wire(
    nodes: Tuple[StructBuildNode, ...], delta_wire: str
) -> Tuple[Tuple[StructBuildNode, ...], int]:
    """Parse a refine round's PATCH-delta wire and apply it → (new forest, patch count).

    The delta wire's PATCH commands address the CURRENT numbered lines (1:1 with
    the text-leaf pre-order). Two op families share the ``PATCH`` command:

    * ``L<n>`` / ``L<n>.a-b`` — a TEXT delta (reuses ``_apply_patches`` verbatim,
      Decision 1's single-line no-shift invariant), mapped back onto the leaves;
    * ``N<id>`` — a STRUCTURAL delta (milestone 2): delete node ``<id>`` + subtree
      (empty inline) or relabel its kind. Applied AFTER the text delta (the text
      addresses stable line indices; the structural delta then renumbers the tree
      for the NEXT round — the resolved-tree hash covers it, Decision 10).

    Count = text patches + structural ops applied (both drive convergence)."""
    patch_cmds = []
    for unit, _terminated in _struct_units(delta_wire):
        cmd = _parse_command_line(unit)
        if cmd is not None and cmd.kind_token.upper() == "PATCH":
            patch_cmds.append(cmd)
    node_patches, line_patch_cmds, _findings = collect_node_patches(patch_cmds)

    # Text delta first (stable line-index address space).
    paths = _text_leaf_paths(nodes)
    lines = _current_numbered_lines(nodes)
    patched, _tfindings, text_count = _apply_patches(lines, line_patch_cmds)
    if text_count:
        replacements = {
            paths[i]: patched[i]
            for i in range(min(len(paths), len(patched)))
            if patched[i] != lines[i]
        }
        nodes = _rewrite_text_at(nodes, replacements)

    # Structural delta second — addresses the SAME (pre-structural) line indices,
    # since the text delta never changes the tree SHAPE, only leaf text.
    struct_count = 0
    if node_patches:
        nodes, struct_count = _apply_structural_patches(nodes, node_patches)
    return nodes, text_count + struct_count


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
    suspects: Sequence[SuspectRegion] = (),
) -> Tuple[str, ...]:
    """The Decision-2 closed trigger set fired on the cold round-1 artifacts.

    Clean pages fire NOTHING → single-pass. Any of: >=1 freeform region; any
    build findings; patches_applied>0 (text OR structural node PATCH); terminator
    compliance <0.98; SINGLE_WITNESS DESPITE a non-empty reading-order witness;
    >=1 deterministic suspect region (§8 — a confidently-garbled read that looks
    clean, so NONE of the other signals fire; this admits the page to the re-read
    pass); (truncation is handled by the caller, which passes a ``truncated``
    reason)."""
    reasons: List[str] = []
    if freeform:
        reasons.append("freeform_region")
    if build.findings:
        reasons.append("findings")
    if build.patches_applied > 0 or getattr(build, "node_patches_applied", 0) > 0:
        reasons.append("patches_applied")
    total = build.total_command_lines
    if total and (build.terminated_command_lines / total) < _TERMINATOR_COMPLIANCE_FLOOR:
        reasons.append("terminator_below_floor")
    if assurance is AssuranceTier.SINGLE_WITNESS and reading_order_text.strip():
        reasons.append("single_witness_with_witness")
    if suspects:
        reasons.append("suspect_region")
    return tuple(reasons)


# --------------------------------------------------------------------------- #
# §8 Level-1 agentic re-read — deterministic suspect surfacing + gated re-read. #
# --------------------------------------------------------------------------- #


def _text_leaves_in_order(
    nodes: Sequence[StructBuildNode],
) -> List[Tuple[Tuple[int, ...], StructBuildNode]]:
    """Pre-order (path, node) of every text-bearing leaf — the reading-order rank.

    Same order + count as ``render_simulacrum_as_numbered_lines`` / the page's
    reading-order ``page_lines`` when the read is faithful, so the Nth text leaf
    aligns to the Nth page line positionally (the bridge that survives a GARBLED
    leaf whose text no longer matches any page line by string)."""
    out: List[Tuple[Tuple[int, ...], StructBuildNode]] = []

    def _walk(n: StructBuildNode, path: Tuple[int, ...]) -> None:
        if (n.text or "").strip():
            out.append((path, n))
        for i, c in enumerate(n.children):
            _walk(c, path + (i,))

    for i, n in enumerate(nodes):
        _walk(n, (i,))
    return out


def _detect_suspects(
    nodes: Sequence[StructBuildNode], page_elements: PageElements
) -> Tuple[SuspectRegion, ...]:
    """Surface deterministic re-read candidates over the resolved text leaves (§8).

    For each text-bearing leaf (skipping freeform / image regions — those already
    have a faithful home): fire the PRIMARY cross-reader-disagreement signal (the
    pdfium text layer over the leaf's region is an INDEPENDENT read) and the
    SECONDARY lexical-implausibility signals. A leaf with NO fired signal is not a
    suspect (clean pages → zero suspects → zero re-reads). SURFACES only — never
    edits.

    The leaf is aligned to its ``PageLine`` FIRST by text (the span-copy bridge),
    and — crucially for a GARBLED leaf whose text no longer matches any line — by
    reading-order RANK as a fallback (the Nth text leaf ↔ the Nth reading-order
    line). The matched line yields both the leaf's bbox (for the crop) and the
    independent pdfium read of the region (for cross-reader disagreement)."""
    line_index = _line_index_by_text(page_elements.page_lines)
    page_lines = page_elements.page_lines
    leaves = _text_leaves_in_order(nodes)
    out: List[SuspectRegion] = []

    for rank, (path, n) in enumerate(leaves):
        is_special = n.kind in (
            SourceDocumentNodeKind.MATH_REGION,
            SourceDocumentNodeKind.VERBATIM_REGION,
            SourceDocumentNodeKind.IMAGE_REGION,
        )
        text = n.text or ""
        if not text.strip() or is_special:
            continue
        # Bridge to a PageLine by SOURCE-LINE INDEX (Decision 8): the Nth text leaf
        # (pre-order, ``_text_leaves_in_order``) ↔ the Nth reading-order line. This is
        # exactly what a garbled leaf needs — its text matches no line by string, but
        # its RANK still locates both its bbox (for the crop) and the independent
        # pdfium read of that region (for cross-reader disagreement). The SAME rank
        # definition binds geometry in ``_lower_with_metadata`` (``leaf_counter``), so
        # detection and lowering agree on which line a leaf owns.
        page_line = _resolve_page_line(text, rank, page_lines, line_index)
        bbox = page_line.bbox if page_line is not None else None
        # Independent read of the region: the pdfium line at this rank/region.
        independent = page_line.text if page_line is not None else ""
        signals: List[str] = []
        cross: Optional[str] = None
        if cross_reader_disagrees(text, independent):
            signals.append("cross_reader_disagreement")
            cross = independent
        signals.extend(lexical_implausibility(text))
        if signals:
            out.append(
                SuspectRegion(
                    node_path=path,
                    bbox=bbox,
                    vision_text=text,
                    signals=tuple(signals),
                    cross_reader=cross,
                )
            )
    return tuple(out)


def _apply_rereads(
    vision,
    manifestation,
    page_num: int,
    nodes: Tuple[StructBuildNode, ...],
    suspects: Sequence[SuspectRegion],
) -> Tuple[Tuple[StructBuildNode, ...], int]:
    """Re-read each suspect region and replace its leaf iff the re-read is better.

    For each suspect with a renderable bbox: render a high-DPI crop + re-read JUST
    that region (``vision.reread_region``). Apply the re-read through the EXISTING
    text-leaf substitution (``_rewrite_text_at``) — the same mechanism a text
    PATCH rides — ONLY when the re-read is more plausible than the incumbent OR
    agrees with the disagreeing cross-reader (firewall: the re-read is never
    authority, only a gated candidate). Truncation / render failure on one region
    is skipped (typed-suspect residue), never sinks the page. Returns
    ``(new_nodes, applied_count)``."""
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerFailure,
        VisionProducerTruncated,
    )
    from lawvm.ingest.suspect_region import _token_agreement

    replacements: Dict[Tuple[int, ...], str] = {}
    applied = 0
    for suspect in suspects[:_MAX_REREADS_PER_PAGE]:
        if suspect.bbox is None:
            continue  # un-renderable region — recorded suspect, no crop possible
        try:
            reread = vision.reread_region(
                manifestation,
                page_num,
                suspect.bbox,
                suspect.vision_text,
                dpi=_REREAD_DPI,
            )
        except (VisionProducerTruncated, VisionProducerFailure):
            continue  # this region stays a typed suspect; the rest proceed
        if not reread or reread == suspect.vision_text:
            continue  # model kept the incumbent (empty) or produced no change
        # Gate: accept the re-read iff it is more plausible OR it agrees with the
        # independent cross-reader that flagged the disagreement.
        agrees_reader = (
            suspect.cross_reader is not None
            and _token_agreement(reread, suspect.cross_reader) >= 0.6
        )
        if more_plausible(reread, suspect.vision_text) or agrees_reader:
            replacements[suspect.node_path] = reread
            applied += 1
    if not replacements:
        return nodes, 0
    return _rewrite_text_at(nodes, replacements), applied


def _cold_read_ladder(appraisal: object, requested_leaf_mode: str) -> Tuple[str, ...]:
    """The ordered cold-read rungs for a page, routed from its appraisal.

    The static ``patch`` default is dead (it silently empties dense pages — the
    controller, not a fixed assumption, chooses the mode). With no appraisal (a
    minimal/fake vision producer) the legacy single read stands. Otherwise: route the
    first rung from the appraisal — untrustworthy lines ⇒ transcribe from the image
    (``inline``), else reference the lines (``span``); an explicit ``span``/``inline``
    request (the A/B-fair modalities) is honoured. Then the ladder the reader climbs
    ONLY on a degenerate (empty) read: route → retry-identical (the MTP decoder is
    nondeterministic, so the cheapest rung is the same call again) → switch mode."""
    if appraisal is None:
        return (requested_leaf_mode,)
    if requested_leaf_mode in ("span", "inline"):
        first = requested_leaf_mode
    else:
        first = "span" if getattr(appraisal, "lines_trustworthy", True) else "inline"
    other = "inline" if first == "span" else "span"
    return (first, first, other)


def _is_degenerate_read(appraisal: object, reconstructed: str) -> bool:
    """A read the appraisal KNOWS is wrong: it saw content but the model returned no
    text-bearing structure (the intermittent empty completion). Non-circular because
    the appraisal is an independent image-first call — this is the signal that makes
    an empty structural read a retry trigger instead of a silently-cached blank."""
    return (
        appraisal is not None
        and getattr(appraisal, "has_content", False)
        and not reconstructed.strip()
    )


# §9 region-decomposition (region-subdivide as a ladder rung). When a whole-page
# cold read TRUNCATES (too dense for the token budget), the whole-page read is the
# coarsest — and lossiest — tiling; subdividing the page into a small number of
# geometric regions and reading each on its OWN crop is more faithful (§9). Bound
# the region count so a page that is STILL too dense per region is typed truncated,
# never subdivided into an unbounded fan-out.
_MAX_SUBDIVIDE_REGIONS = 8
# The DPI a region crop is read at — the same zoom the §8 re-read uses. What matters
# is that a region crop concentrates the encoder's fixed token grid on a SMALLER area
# (isolation), so its text is transcribed faithfully; the DPI just sets crop sharpness.
_SUBDIVIDE_DPI = _REREAD_DPI

# --------------------------------------------------------------------------- #
# Batched "thumbnail + tiles" scanned-page read (§9, SOTA high-res-VLM path).   #
# --------------------------------------------------------------------------- #
#
# The per-region subdivide reads each region on its OWN request — correct, but it
# re-sends the system prompt + an image N times (input tokens ≈ N× a single read).
# The batched path sends ONE request carrying a low-res whole-page THUMBNAIL (global
# context + reading order, cheap) plus the high-res region TILES (each isolated so
# its glyphs command the encoder grid), and parses the per-region transcriptions
# back by their ``I{N}`` label. ONE system-prompt overhead + one round-trip + one
# failure roll on the flaky backend, at a fraction of the input tokens — while
# KEEPING the isolation that recovers small glyphs. Gated to the scanned residual;
# the per-region path stays the fallback (a producer without ``read_page_tiled``, a
# truncated batch, or ``LAWVM_INGEST_TILED_READ=0`` all route to it).
_TILED_READ_ENABLED = (os.environ.get("LAWVM_INGEST_TILED_READ", "1") or "1") != "0"
# Whole-page thumbnail render scale (≈0.5 ⇒ ~36 DPI on an A4 gazette) — smallest
# that still conveys reading order / layout; the crops carry the readable pixels.
_TILED_THUMBNAIL_SCALE = float(
    os.environ.get("LAWVM_INGEST_TILED_THUMB_SCALE", "0.5") or "0.5"
)
# Per-tile crop DPI (isolation, not DPI, is the fidelity lever — see _SUBDIVIDE_DPI).
_TILED_CROP_DPI = _REREAD_DPI
# Chunk threshold: at most this many tiles per batched request. Beyond it the crops
# are split across several batched requests (each still one shared prompt) so one
# request never exceeds the backend's context / pixel budget.
_TILED_MAX_TILES = _MAX_SUBDIVIDE_REGIONS


def _union_bbox(lines: Sequence[PageLine]) -> Optional[BBox]:
    """The union bbox of a group of page lines (None when none carry geometry)."""
    boxes = [pl.bbox for pl in lines if pl.bbox is not None]
    if not boxes:
        return None
    return BBox(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def _split_contiguous(
    seq: Sequence[PageLine], k: int
) -> List[Sequence[PageLine]]:
    """Split a reading-ordered line sequence into ``k`` near-equal contiguous chunks."""
    n = len(seq)
    k = max(1, min(k, n))
    size = -(-n // k)  # ceil division → chunks of size, last shorter
    return [seq[i : i + size] for i in range(0, n, size)]


def _propose_regions(
    page_elements: PageElements, max_regions: int
) -> Tuple[Tuple[BBox, int], ...]:
    """Deterministic geometric read regions for a truncated page (§9).

    Subdivides by COLUMN (``geom.col``, left→right) then by vertical BAND within a
    column (contiguous reading-order chunks, top→bottom). Region count is bounded by
    ``max_regions`` (bands-per-column budget = ``max_regions // n_columns``), so a
    dense single-column page is sliced vertically and a multi-column page is split by
    column first. Returns ``(bbox, expected_line_count)`` pairs in the deterministic
    (column, band, y) reading order — NEVER completion order. Empty when the page has
    no usable geometry or cannot be split into >= 2 regions (→ the caller types the
    page truncated rather than pretend-subdivide)."""
    geo = [pl for pl in page_elements.page_lines if pl.bbox is not None]
    if len(geo) < 2:
        return ()
    # Columns: distinct ``col`` values (None → a single implicit column), ordered
    # left→right by their leftmost edge (a deterministic reading order for columns).
    cols: Dict[int, List[PageLine]] = {}
    for pl in geo:
        cols.setdefault(pl.col if pl.col is not None else 0, []).append(pl)
    # Every ``pl`` here has a non-None bbox (``geo`` was filtered above); the min/sort
    # keys read ``bbox`` fields directly (ty can't narrow across the comprehension).
    col_keys = sorted(
        cols, key=lambda c: min(pl.bbox.x0 for pl in cols[c] if pl.bbox is not None)
    )
    n_cols = len(col_keys)
    bands_per_col = max(1, max_regions // n_cols)
    regions: List[Tuple[Tuple[int, int], BBox, int]] = []
    for ci, ck in enumerate(col_keys):
        # Reading order within a column: top→bottom (descending y), then left edge,
        # then the extractor's own y_order as a stable tiebreak.
        col_lines = sorted(
            cols[ck],
            key=lambda pl: (
                (-pl.bbox.y1, pl.bbox.x0, pl.y_order)
                if pl.bbox is not None
                else (0.0, 0.0, pl.y_order)
            ),
        )
        for bi, chunk in enumerate(_split_contiguous(col_lines, bands_per_col)):
            bbox = _union_bbox(chunk)
            if bbox is None or not chunk:
                continue
            regions.append(((ci, bi), bbox, len(chunk)))
    if len(regions) < 2:
        return ()
    regions.sort(key=lambda r: r[0])  # (column, band) — deterministic reading order
    return tuple((bbox, n) for _key, bbox, n in regions)


def _read_regions_for(
    manifestation,
    page_num: int,
    page_elements: PageElements,
    max_regions: int,
) -> Tuple[Tuple[BBox, int], ...]:
    """The read regions for a page (§9), shared by the per-region + batched readers.

    Prefers the pdfium text-layer GEOMETRY (``_propose_regions`` — column → band); a
    genuinely SCANNED page has none, so it falls back to the page-IMAGE segmentation
    (``visual.segment_page_regions`` — recursive XY-cut over the ink projection).
    Empty when the page has no usable geometry AND cannot be image-segmented."""
    regions = _propose_regions(page_elements, max_regions)
    if not regions:
        # No pdfium text-layer geometry (a genuinely SCANNED page) — derive the read
        # regions from the page IMAGE instead (recursive XY-cut over the ink
        # projection), so the scanned residual can subdivide too. Empty when the
        # image cannot be segmented, and the caller then types the page truncated.
        from lawvm.ingest.visual import segment_page_regions

        regions = segment_page_regions(manifestation, page_num)
    return regions


def _subdivide_page_read(
    vision,
    manifestation,
    page_num: int,
    page_elements: PageElements,
    *,
    max_regions: int = _MAX_SUBDIVIDE_REGIONS,
    dpi: int = _SUBDIVIDE_DPI,
) -> Tuple[Tuple[StructBuildNode, ...], int, bool]:
    """Read a truncated page region-by-region and stitch the trees → (nodes, regions_read, complete).

    §9: the whole-page read truncated (dropped the page tail), so read each geometric
    region on its OWN crop via the EXISTING cold region reader
    (``vision.read_region_cold`` — content-addressed, under ``PDFIUM_LOCK``) and
    stitch every transcribed physical line into a flat PARAGRAPH forest, assembled in
    the deterministic region order (``_propose_regions`` — column, band, y), NEVER
    completion order (determinism firewall). When the page has NO pdfium text-layer
    geometry (a genuinely SCANNED page), the regions are instead derived from the
    page IMAGE (``visual.segment_page_regions`` — recursive XY-cut), so the scanned
    stratum subdivides too. A minimal/fake vision producer WITHOUT
    ``read_region_cold`` cannot subdivide → ``((), 0, False)`` (the caller types the
    page truncated). ``complete`` is False when ANY region itself truncated / failed
    (that region is still too dense) — the caller then types the page truncated while
    KEEPING whatever was read (never a silent drop)."""
    if not hasattr(vision, "read_region_cold"):
        return (), 0, False
    regions = _read_regions_for(manifestation, page_num, page_elements, max_regions)
    if not regions:
        return (), 0, False
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerFailure,
        VisionProducerTruncated,
    )

    nodes: List[StructBuildNode] = []
    regions_read = 0
    complete = True
    for bbox, expected in regions:  # already in deterministic reading order
        try:
            text = vision.read_region_cold(
                manifestation, page_num, bbox, dpi=dpi, expected_lines=expected
            )
        except (VisionProducerTruncated, VisionProducerFailure):
            # This region is still too dense (or un-renderable) → the page is not
            # fully subdivided. Keep the other regions; the caller types it truncated.
            complete = False
            continue
        if not text.strip():
            continue
        regions_read += 1
        for physical_line in text.split("\n"):
            s = physical_line.strip()
            if s:
                nodes.append(
                    StructBuildNode(
                        kind=SourceDocumentNodeKind.PARAGRAPH, text=s
                    )
                )
    return tuple(nodes), regions_read, complete


def _stitch_region_texts(
    texts: Sequence[str],
) -> Tuple[Tuple[StructBuildNode, ...], int]:
    """Flatten per-region transcriptions → a flat PARAGRAPH forest + non-empty count.

    Shared by the batched reader: one PARAGRAPH node per non-blank physical line, in
    the regions' reading order (the tiled reply is parsed back into region order by
    ``I{N}`` label), NEVER completion order (determinism firewall)."""
    nodes: List[StructBuildNode] = []
    regions_read = 0
    for text in texts:
        if not text.strip():
            continue
        regions_read += 1
        for physical_line in text.split("\n"):
            s = physical_line.strip()
            if s:
                nodes.append(
                    StructBuildNode(kind=SourceDocumentNodeKind.PARAGRAPH, text=s)
                )
    return tuple(nodes), regions_read


def _subdivide_page_read_tiled(
    vision,
    manifestation,
    page_num: int,
    page_elements: PageElements,
    *,
    max_regions: int = _MAX_SUBDIVIDE_REGIONS,
    crop_dpi: int = _TILED_CROP_DPI,
    thumbnail_scale: float = _TILED_THUMBNAIL_SCALE,
    max_tiles: int = _TILED_MAX_TILES,
) -> Tuple[Tuple[StructBuildNode, ...], int, bool]:
    """Batched "thumbnail + tiles" region read → (nodes, regions_read, complete).

    §9 SOTA path: ONE request per tile-chunk carrying the low-res whole-page
    thumbnail + the chunk's high-res region crops (``vision.read_page_tiled``), whose
    per-region transcriptions are parsed back by ``I{N}`` label and stitched flat in
    reading order. When the region count exceeds ``max_tiles`` the crops are split
    across several batched requests (each still ONE shared system prompt) so a single
    request never blows the backend's context / pixel budget. A producer WITHOUT
    ``read_page_tiled`` cannot batch → ``((), 0, False)`` (the caller falls back to
    the per-region reader). ``complete`` is False when ANY chunk truncated / failed —
    the caller then falls back / types the page truncated while KEEPING what was read
    (never a silent drop)."""
    if not hasattr(vision, "read_page_tiled"):
        return (), 0, False
    regions = _read_regions_for(manifestation, page_num, page_elements, max_regions)
    if not regions:
        return (), 0, False
    from lawvm.ingest.llm_backends.vision_producer import (
        VisionProducerFailure,
        VisionProducerTruncated,
    )

    nodes: List[StructBuildNode] = []
    regions_read = 0
    complete = True
    for start in range(0, len(regions), max_tiles):
        chunk = regions[start : start + max_tiles]
        try:
            texts = vision.read_page_tiled(
                manifestation,
                page_num,
                chunk,
                thumbnail_scale=thumbnail_scale,
                crop_dpi=crop_dpi,
            )
        except (VisionProducerTruncated, VisionProducerFailure):
            # A truncated / malformed chunk → not fully read. Keep the other chunks;
            # the caller falls back to the per-region reader or types it truncated.
            complete = False
            continue
        chunk_nodes, chunk_read = _stitch_region_texts(texts)
        nodes.extend(chunk_nodes)
        regions_read += chunk_read
    return tuple(nodes), regions_read, complete


def _scanned_region_read(
    vision,
    manifestation,
    page_num: int,
    page_elements: PageElements,
) -> Tuple[Tuple[StructBuildNode, ...], int, bool]:
    """Scanned-stratum region read: batched thumbnail+tiles FIRST, per-region fallback.

    The batched path (``_subdivide_page_read_tiled``) is the default (§9 SOTA) — ONE
    shared prompt + one round-trip per tile-chunk instead of N separate region reads.
    It applies ONLY here (the scanned / no-text-geometry residual); born-digital pages
    never reach this. Falls back to the per-region reader (``_subdivide_page_read``)
    when batching is disabled (``LAWVM_INGEST_TILED_READ=0``), the producer lacks
    ``read_page_tiled``, or a batch came back incomplete/empty — so the correct,
    higher-cost path always backstops the cheap one (never a silent drop)."""
    if _TILED_READ_ENABLED and hasattr(vision, "read_page_tiled"):
        nodes, regions_read, complete = _subdivide_page_read_tiled(
            vision, manifestation, page_num, page_elements
        )
        if nodes and complete:
            return nodes, regions_read, complete
    return _subdivide_page_read(vision, manifestation, page_num, page_elements)


def _synthetic_subdivide_build(
    nodes: Tuple[StructBuildNode, ...]
) -> StructBuildResult:
    """A stitched-region forest as a ``StructBuildResult`` (no wire — code-assembled).

    The region stitch is assembled by code, not parsed from a model wire, so it has
    no command-line accounting: zero findings, zero patches, and ``total_command_lines
    = 0`` so the terminator-compliance floor is skipped (``_gate_reasons``). The
    ``subdivided`` gate reason is added by the caller."""
    return StructBuildResult(
        roots=nodes,
        findings=(),
        terminator_used=False,
        total_command_lines=0,
        terminated_command_lines=0,
        patches_applied=0,
        node_patches_applied=0,
    )


def _page_lacks_text_geometry(page_elements: PageElements) -> bool:
    """True when the page has NO pdfium text-layer geometry — a genuinely SCANNED page.

    The whole-page vision read loses small text on such a page (the encoder grid),
    and the geometry-driven §9 subdivide cannot run (no per-line bboxes). Detecting
    this routes the page to the IMAGE-segmented region read (``segment_page_regions``)
    by default. A page whose extractor bound ANY per-line bbox is NOT scanned in this
    sense — its geometry lane is left untouched."""
    return not any(pl.bbox is not None for pl in page_elements.page_lines)


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
    truncation. A refine round may now change STRUCTURE (milestone 2): a ``N<id>``
    node PATCH deletes a duplicated/hallucinated node + subtree or relabels a
    mis-kinded block. A delete/relabel RENUMBERS the rendered lines, adding a
    second oscillation axis (Decision 10) — the resolved-tree fixpoint hash already
    covers structural change, so a delete↔re-add cycle terminates via the
    earlier-round hash re-entry guard (keep-last, flagged)."""
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

    # Appraise-first (§ agentic, image-first): a cheap verdict the ladder routes on.
    # OPTIONAL — a minimal/fake vision producer without ``appraise_page`` keeps the
    # legacy single cold read (appraisal is None → ladder is just the requested mode).
    appraisal = None
    if hasattr(vision, "appraise_page"):
        appraisal = vision.appraise_page(manifestation, page_num, page_elements)
        if not appraisal.has_content:
            # The MODEL saw a blank page — zero cold reads, and (crucially) this is
            # never confusable with a degenerate empty read (that needs has_content).
            return ConvergedPage(
                nodes=(),
                convergence=ConvergenceInfo(
                    rounds=0,
                    round_hashes=(),
                    termination="appraised_blank",
                    gate_reasons=("appraised_blank",),
                    patches_total=0,
                    rereads=0,
                    read_attempts=0,
                ),
                freeform=(),
                assurance=AssuranceTier.SINGLE_WITNESS,
                raw_wire_digests=(),
            )

    # Cold-read ladder: climb a rung ONLY on a degenerate read (appraisal saw content
    # but the model returned no structure). The static ``patch`` default is gone.
    raw_digests: List[str] = []
    build = None
    nodes: Tuple[StructBuildNode, ...] = ()
    reconstructed = ""
    read_attempts = 0
    subdivided = False
    regions_read = 0
    truncated_cold = False

    # §9 SCANNED-page default: a page with no pdfium text geometry is read whole ONLY
    # by the vision model, whose encoder under-samples small text (a 6-8 pt italic
    # heading misreads at ANY whole-page render scale — the grid, not the DPI). Read
    # it region-by-region FROM THE PAGE IMAGE up front (``segment_page_regions``) so
    # each region's text commands enough of the encoder grid to transcribe faithfully
    # — the same fidelity lever the §8 re-read uses, applied by DEFAULT to the scanned
    # residual rather than only on a suspect flag. ``_scanned_region_read`` prefers the
    # BATCHED thumbnail+tiles path (one shared prompt + one round-trip per tile-chunk),
    # falling back to the per-region reader. Only when the image cannot be segmented
    # (or a batch/region truncates all the way) does the whole-page ladder below run.
    if (
        appraisal is not None
        and _page_lacks_text_geometry(page_elements)
        and (hasattr(vision, "read_region_cold") or hasattr(vision, "read_page_tiled"))
    ):
        sub_nodes, regions_read, complete = _scanned_region_read(
            vision, manifestation, page_num, page_elements
        )
        if sub_nodes and complete:
            subdivided = True
            nodes = sub_nodes
            build = _synthetic_subdivide_build(nodes)
            reconstructed = "\n".join(_struct_text_of(n) for n in nodes)
            read_attempts = regions_read

    ladder = () if subdivided else _cold_read_ladder(appraisal, leaf_mode)
    for attempt_leaf_mode in ladder:
        try:
            result = vision.propose_page_struct(
                manifestation, page_num, page_elements, leaf_mode=attempt_leaf_mode
            )
        except VisionProducerTruncated:
            # The whole-page cold read is too dense for the token budget — accepting
            # its tree DROPS the page tail. Break to the §9 region-subdivide rung
            # (below) instead of accepting a truncated read.
            truncated_cold = True
            break
        read_attempts += 1
        build = result.build
        nodes = build.roots
        raw_digests.append(hashlib.sha256(result.raw_content.encode("utf-8")).hexdigest())
        reconstructed = "\n".join(_struct_text_of(n) for n in nodes)
        if not _is_degenerate_read(appraisal, reconstructed):
            break
    else:
        if not subdivided:
            # Every rung came back degenerate → a TYPED unreadable page (fail-loud),
            # NOT a silently-cached empty read.
            return ConvergedPage(
                nodes=nodes,
                convergence=ConvergenceInfo(
                    rounds=1,
                    round_hashes=(_resolved_tree_hash(nodes),),
                    termination="unreadable_page",
                    gate_reasons=("unreadable_page",),
                    patches_total=build.patches_applied if build is not None else 0,
                    rereads=0,
                    read_attempts=read_attempts,
                ),
                freeform=(),
                assurance=AssuranceTier.UNADJUDICATED_PROPOSAL,
                raw_wire_digests=tuple(raw_digests),
            )

    # §9 region-subdivide rung: a truncated whole-page cold read is the COARSEST,
    # lossiest tiling. Subdivide the page into a bounded number of geometric regions
    # (column → vertical band), read each on its OWN crop via the existing cold region
    # reader, and stitch the region trees into the forest in a deterministic (column,
    # band, y) order. The stitched forest then flows through the SAME gate + refine +
    # §8 re-read pass as any cold read (firewall preserved).
    if truncated_cold:
        sub_nodes, regions_read, complete = _subdivide_page_read(
            vision, manifestation, page_num, page_elements
        )
        if not (sub_nodes and complete):
            # Subdivision impossible (no region reader / no usable geometry) OR a
            # region is STILL too dense → typed truncated (fail-loud), keeping any
            # regions that WERE read. NEVER a silent drop of the page tail.
            return ConvergedPage(
                nodes=sub_nodes,
                convergence=ConvergenceInfo(
                    rounds=1,
                    round_hashes=(_resolved_tree_hash(sub_nodes),),
                    termination="truncated",
                    gate_reasons=("truncated",)
                    + (("subdivided",) if regions_read else ()),
                    patches_total=0,
                    rereads=0,
                    read_attempts=read_attempts,
                    regions_read=regions_read,
                ),
                freeform=_freeform_index(sub_nodes),
                assurance=AssuranceTier.UNADJUDICATED_PROPOSAL,
                raw_wire_digests=tuple(raw_digests),
            )
        subdivided = True
        nodes = sub_nodes
        build = _synthetic_subdivide_build(nodes)
        reconstructed = "\n".join(_struct_text_of(n) for n in nodes)

    # Past the ladder + subdivide rung, a non-degenerate read always set ``build``
    # (a truncated / degenerate ladder returned early above).
    assert build is not None
    freeform = _freeform_index(nodes)
    assurance = _page_assurance(reconstructed, reading_order_text, adjudicator, region)

    # §8: surface deterministic re-read candidates on the cold read. A confidently
    # garbled leaf fires NONE of the other gate signals (looks clean), so a suspect
    # is its OWN gate trigger — the page enters the refine + re-read pass.
    suspects = _detect_suspects(nodes, page_elements)
    gate_reasons = _gate_reasons(build, assurance, freeform, reading_order_text, suspects)
    if subdivided:
        # A subdivided page always records the §9 rung (even if no other gate fired),
        # so it enters the normal refine/re-read pass rather than single-passing.
        gate_reasons = gate_reasons + ("subdivided",)
    round_hashes: List[str] = [_resolved_tree_hash(nodes)]
    patches_total = build.patches_applied
    rereads = 0

    if not gate_reasons:
        return ConvergedPage(
            nodes=nodes,
            convergence=ConvergenceInfo(
                rounds=1,
                round_hashes=tuple(round_hashes),
                termination="gated_single_pass",
                gate_reasons=(),
                patches_total=patches_total,
                rereads=0,
                read_attempts=read_attempts,
                regions_read=regions_read,
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

    # §8 agentic re-read pass: re-detect suspects on the CONVERGED tree (the refine
    # rounds may have already fixed some) and re-read each renderable garble at high
    # DPI, replacing its leaf through the SAME gated text-substitution the refine
    # loop uses (firewall: never authority). A re-read that lands mutates the
    # resolved tree, so its hash is appended (the fixpoint key stays honest).
    if hasattr(vision, "reread_region"):
        final_suspects = _detect_suspects(nodes, page_elements)
        if final_suspects:
            reread_nodes, applied = _apply_rereads(
                vision, manifestation, page_num, nodes, final_suspects
            )
            if applied:
                nodes = reread_nodes
                patches_total += applied
                rereads += applied
                round_hashes.append(_resolved_tree_hash(nodes))

    freeform = _freeform_index(nodes)
    return ConvergedPage(
        nodes=nodes,
        convergence=ConvergenceInfo(
            rounds=rounds,
            round_hashes=tuple(round_hashes),
            termination=termination,
            gate_reasons=gate_reasons,
            patches_total=patches_total,
            rereads=rereads,
            read_attempts=read_attempts,
            regions_read=regions_read,
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
    page_lines: Sequence[PageLine],
    recurrence: Mapping[str, int],
    page_count: int,
    witness_words: set,
    converged: bool,
    y_counter: List[int],
    leaf_counter: List[int],
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

    # Bind the node's geometry to its ``PageLine`` by SOURCE-LINE INDEX (Decision 8):
    # the Nth text-bearing leaf (pre-order) ↔ the Nth reading-order ``page_line``.
    # Rank-primary binding survives a converge/re-read text correction (the leaf no
    # longer matches any line by string but its rank still locates its bbox) and
    # gives recurring identical lines their OWN geometry instead of all collapsing
    # onto the first occurrence. Only text-bearing leaves consume a rank (matching
    # ``page_lines`` reading order); pure containers / images do not.
    has_text = bool(node.text and node.text.strip())
    if has_text:
        rank = leaf_counter[0]
        leaf_counter[0] += 1
        page_line = _resolve_page_line(node.text, rank, page_lines, line_index)
    else:
        page_line = None
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
            page_lines=page_lines,
            recurrence=recurrence,
            page_count=page_count,
            witness_words=witness_words,
            converged=converged,
            y_counter=y_counter,
            leaf_counter=leaf_counter,
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
    page_lines = page_elements.page_lines
    rec = recurrence if recurrence is not None else {}
    witness_words = _witness_words(reading_order_text)
    converged_flag = converged.convergence.termination in ("empty_patch", "fixpoint")

    y_counter = [0]
    # Reading-order rank of the next text-bearing leaf — the source-line index the
    # geometry bridge binds by (Decision 8). Threaded (not per-node) so the pre-order
    # leaf sequence stays 1:1 with ``page_lines`` across the whole forest.
    leaf_counter = [0]
    nodes = tuple(
        _lower_with_metadata(
            n,
            region=region,
            digest=digest,
            page_num=page_num,
            tier=converged.assurance,
            line_index=line_index,
            page_lines=page_lines,
            recurrence=rec,
            page_count=page_count,
            witness_words=witness_words,
            converged=converged_flag,
            y_counter=y_counter,
            leaf_counter=leaf_counter,
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
            "rereads": sim.convergence.rereads,
            "read_attempts": sim.convergence.read_attempts,
            "regions_read": sim.convergence.regions_read,
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
            rereads=conv.get("rereads", 0),
            read_attempts=conv.get("read_attempts", 1),
            regions_read=conv.get("regions_read", 0),
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
    max_workers: Optional[int] = None,
    struct_geom: bool = False,
) -> Tuple[PageSimulacrum, ...]:
    """Produce the ``Sequence[PageSimulacrum]`` for a manifestation (§1 interface out).

    **Born-digital fast path (``struct_geom=True``, default OFF).** When enabled,
    every page whose pdfium text layer is dense enough (``born_digital.page_is_born_digital``)
    is read DETERMINISTICALLY from geometry + the text layer — NO image sent to the
    vision model (``ingest.born_digital``). Text-poor pages fall back to the vision
    ``converge_page`` lane unchanged. This is an OPT-IN token lever (~8.7k image
    tokens/page saved per born-digital page); it must be A/B-proven not-worse before
    it is ever made default and NEVER silently replaces the vision lane.

    Runs the recurrence pre-pass over ALL pages first (whole-doc furniture
    affordance, §4), then processes the pages through ``converge_page`` (gate +
    patch-to-fixpoint) → ``build_page_simulacrum`` (metadata + tripwire) in a
    BOUNDED ``ThreadPoolExecutor``. The result is the Level-1 → Level-2 bridge;
    persist it via ``ParsedIrStore.put_page_simulacrum`` so re-running Level 2
    never re-runs the model (Decision 11).

    **Per-PDF GPU saturation (§ pipeline concurrency).** Level-1 per-page simulacra
    are INDEPENDENT by design (§1: no cross-page reasoning at Level 1), so the
    per-page vision work is embarrassingly parallel. Each worker runs the existing
    ``converge_page`` SYNCHRONOUSLY — its vision HTTP calls (``propose_page_struct``
    / ``propose_page_patch_delta`` / ``reread_region`` / ``read_region_cold``) then
    overlap across workers, keeping the GPU fed instead of idling between serial
    pages. pdfium rendering inside those calls is serialized by the shared
    ``ingest.visual.PDFIUM_LOCK``, so concurrent workers parallelize inference while
    still serializing every pdfium touch safely.

    **Determinism is index-ordered, never completion-ordered (review Decision 7).**
    Each page's simulacrum is already deterministic (temp=0, content-addressed); the
    workers write into an index-slotted list and the tuple is assembled STRICTLY by
    page index (reading order). The output is therefore BYTE-IDENTICAL to the serial
    version for the same inputs regardless of worker count or which page finishes
    first. ``max_workers`` (or ``LAWVM_INGEST_PAGE_CONCURRENCY``) is a throughput
    knob ONLY — it cannot change the result. A per-page exception is contained to its
    own worker (siblings still complete) and re-raised deterministically at the
    LOWEST failing page index, matching the serial loop's fail-at-first-bad-page
    order (fail-loud — never a silently dropped page)."""
    page_count = min(len(reading_order_pages), max_pages)
    if page_count == 0:
        return ()
    all_elements: List[PageElements] = [
        page_element_producer.page_elements(manifestation.source_bytes, i + 1)
        for i in range(page_count)
    ]
    recurrence = band_recurrence_map(all_elements)

    def _simulacrum_for_index(idx: int) -> PageSimulacrum:
        page_num = idx + 1
        pe = all_elements[idx]
        ro_text = reading_order_pages[idx]
        # Attribute every vision model call this worker issues to its (pdf, page)
        # unit for the token/throughput ledger. The tag MUST be set here — inside the
        # ThreadPool worker body — because the meter's unit stack is thread-local and
        # a worker thread does not inherit the submitting thread's context. This is a
        # transparent observability wrapper: it changes no result (determinism firewall).
        # Born-digital fast path (opt-in): a dense-text-layer page is read
        # deterministically from geometry — NO vision call (the token lever). A
        # text-poor page falls through to the vision converge lane unchanged.
        if struct_geom:
            from lawvm.ingest.born_digital import born_digital_page, page_is_born_digital

            if page_is_born_digital(pe):
                return born_digital_page(
                    manifestation,
                    page_num,
                    pe,
                    recurrence=recurrence,
                    page_count=page_count,
                ).simulacrum
        with meter_unit(pdf=manifestation.locator, page=page_num):
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
            return build_page_simulacrum(
                converged,
                manifestation,
                page_num,
                pe,
                reading_order_text=ro_text,
                recurrence=recurrence,
                page_count=page_count,
            )

    workers = max_workers if max_workers is not None else _DEFAULT_PAGE_CONCURRENCY
    workers = max(1, min(workers, page_count))

    # Submit every page, then collect BY INDEX (never completion order). Calling
    # ``.result()`` in index order re-raises the lowest-index failure first — the
    # same page the serial loop would have raised on — so the fail-loud behavior is
    # deterministic and byte-identical for the successful prefix. Siblings already
    # ran concurrently, so one bad page never prevents the others from computing.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_simulacrum_for_index, idx) for idx in range(page_count)]
        out: List[PageSimulacrum] = [f.result() for f in futures]
    return tuple(out)
