"""Frozen Level-1 interface carriers — the per-page faithful simulacrum.

FROZEN at the end of Track A (§5.5 of ``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``);
Tracks B (Level-1 producer) and C (Level-2 de-facsimile) compile against these.
CARRIERS ONLY — no orchestration/convergence logic here (that is Track B).

A ``PageSimulacrum`` is the immutable EVIDENCE record for one page: a faithful
tree (furniture KEPT as ``hint.furniture`` nodes, metadata v1 on ``attrs``), an
index of freeform escape-hatch regions, per-page convergence metadata, and the
page-level assurance tier. ``SpanRef`` addresses back into the frozen tree so a
Level-2 ledger claim is reversible against the persisted evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNode


@dataclass(frozen=True, slots=True)
class SpanRef:
    """Ledger → immutable-evidence address: a child-index path into a page tree.

    ``node_path`` is a tuple of child indices walking ``PageSimulacrum.nodes``
    (the tree is frozen, so a path is a stable identity). Addresses ONLY the
    final persisted simulacrum (Decision 10) — intermediate convergence rounds
    are debug evidence, never ledger targets.
    """

    page_num: int
    node_path: Tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FreeformRegion:
    """One Level-1 freeform escape hatch (math / verbatim) in a page tree.

    Bbox-anchored, never pixel-copied; the reason is a closed vocabulary so the
    escape hatch stays rate-limited by construction (clean pages emit zero).
    """

    node_path: Tuple[int, ...]
    kind: str  # "math" | "verbatim"
    reason: str  # marginalia|complex_layout|image_baked|garbled_source|ambiguous|rotated|handwritten
    bbox: Optional[BBox]


@dataclass(frozen=True, slots=True)
class ConvergenceInfo:
    """Per-page patch-to-convergence metadata (Decision 2 + Decision 10).

    ``round_hashes`` is the canonical resolved-tree SHA per round (post-PATCH,
    post-span-copy); ``termination`` and ``gate_reasons`` draw from closed
    vocabularies so the convergence outcome is auditable, not free text.

    ``rereads`` (§8, additive) counts the Level-1 agentic re-reads APPLIED — a
    suspect (garbled) region re-read at higher DPI whose result replaced the leaf
    through the normal gated PATCH. Zero for a clean page (no suspects → no
    re-reads, output-sparse); a page with a garble also carries the
    ``suspect_region`` gate reason.
    """

    rounds: int
    round_hashes: Tuple[str, ...]
    termination: str  # empty_patch|fixpoint|oscillation|max_iters|gated_single_pass|truncated
    gate_reasons: Tuple[str, ...]  # Decision 2 closed trigger set (+ suspect_region, §8)
    patches_total: int
    rereads: int = 0


@dataclass(frozen=True, slots=True)
class PageSimulacrum:
    """One page's faithful, immutable simulacrum — the Level-1 → Level-2 bridge.

    ``nodes`` is the faithful page tree with furniture KEPT (``hint.furniture=1``)
    and ``attrs`` carrying metadata v1 (see ``ingest.metadata``). Persisted as a
    per-page evidence record so re-running Level 2 never re-runs the model.
    """

    page_num: int
    nodes: Tuple[SourceDocumentNode, ...]
    freeform: Tuple[FreeformRegion, ...]
    convergence: ConvergenceInfo
    assurance: AssuranceTier
    raw_wire_digests: Tuple[str, ...]
