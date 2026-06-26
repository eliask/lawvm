"""SurfaceLens protocol, minimal source substrate, and lens result seeds.

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt`` §D2
(lens contract), §D4 (minimal substrate-first).

A lens emits **seeds**, not final graph nodes/edges. The assembler
(`legal_surface_assembler.py`) mints stable IDs, validates statuses,
deduplicates, adds entity nodes, and rejects malformed seeds. New node types
therefore require registry/lens entries — never bespoke assembler code.

Substrate rule (§D4):

    Lenses receive a SourceSurfaceBundle.
    Lenses must NOT fetch source text themselves.

``token_tape`` / ``morph_overlay`` are optional future views on a
``SourceSurfaceUnit`` (Phase 7). v0 lenses may tokenize internally from
``raw_text``; the substrate shape does not change when they migrate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

from lawvm.core.legal_surface_graph import (
    AuthorityRole,
    SourceSpanRef,
    SurfaceDiagnostic,
    SurfaceGraphSubject,
)

# ── Minimal source substrate (§D4) ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceSurfaceUnit:
    """One addressable unit of source text the lenses analyze.

    ``token_tape`` / ``morph_overlay`` are reserved for the Phase 7 migration
    and stay ``None`` in v0.
    """

    source_unit_id: str
    work_id: str
    address: str | None
    raw_text: str
    source_hash: str
    source_ref: SourceSpanRef
    effective_interval: tuple[str | None, str | None] = (None, None)
    metadata: Mapping[str, object] = field(default_factory=dict)

    # Raw source bytes (e.g. the statute's AKN XML) — a TYPED view for the
    # adapter lenses that genuinely need to re-parse the original markup tree
    # (the §D4 Stage-1 bridge). This replaces the former free-form
    # ``metadata["xml_bytes"]`` dict channel with a typed field so the byte
    # origin is part of the unit schema rather than an untyped key. Read it via
    # :func:`source_bytes_of`, which fails loud (returns ``None``) on absence so
    # a missing-source view stays a typed residual, never a silent skip. Like
    # ``token_tape`` this is a view a later pass consumes, NOT a graph input (the
    # assembler's graph_id never folds unit views), so populating it cannot
    # perturb the assembled surface graph.
    source_bytes: bytes | None = None

    # future optional views (Phase 7)
    token_tape: object | None = None
    morph_overlay: object | None = None
    # Optional clause/sentence segmentation view (a ``ClauseIndex``). Additive:
    # default ``None``, and the assembler's graph_id is computed only over node/
    # edge payloads + the subject — never over unit views — so populating this
    # cannot perturb the graph. Later attachment passes query it to ask "which
    # clause/sentence owns this span?" instead of a magic char window.
    clause_index: object | None = None


@dataclass(frozen=True, slots=True)
class SourceSurfaceBundle:
    """The complete substrate a lens is permitted to read.

    Lenses must read ONLY from this bundle — never fetch source independently.
    """

    jurisdiction: str
    subject: SurfaceGraphSubject
    units: tuple[SourceSurfaceUnit, ...]


def source_bytes_of(unit: SourceSurfaceUnit) -> bytes | None:
    """Typed accessor for a unit's raw source bytes (the §D4 bridge view).

    Returns the unit's :attr:`SourceSurfaceUnit.source_bytes` when present, or
    ``None`` when the unit carries no raw-byte view. The caller treats ``None``
    as a fail-loud miss (a typed residual), never a silent skip.

    This is the single typed entry point adapter lenses use to reach the raw
    markup; it replaces ad-hoc ``unit.metadata["xml_bytes"]`` reach-back so the
    byte origin flows through the unit schema instead of an untyped dict key.
    """
    return unit.source_bytes


# ── Analysis context ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceAnalysisContext:
    """Read-only context passed to a lens beyond the source bundle.

    v0 carries the surface time and a free-form options mapping; richer shared
    services (registries, morph services) attach here later without changing
    the lens signature.
    """

    surface_time: str | None = None
    options: Mapping[str, object] = field(default_factory=dict)


# ── Seeds emitted by lenses (§D2) ────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SurfaceNodeSeed:
    """A node a lens proposes. The assembler mints the final ``node_id``."""

    node_kind: str
    source_ref: SourceSpanRef | None
    local_discriminator: str
    rule_id: str
    node_status: str
    payload: Mapping[str, object]
    authority_role: AuthorityRole = "surface_fact"


@dataclass(frozen=True, slots=True)
class SurfaceEdgeSeed:
    """An edge a lens (or edge pass) proposes.

    ``src_local`` / ``dst_local`` carry the seed-local identifiers a lens uses
    to refer to its own node seeds (the seed's ``local_discriminator`` keyed by
    node_kind, or — for cross-lens edge passes — an already-minted ``node_id``).
    The assembler resolves them to minted node ids and validates both
    endpoints exist.
    """

    edge_kind: str
    src_local: str
    dst_local: str
    rule_id: str
    surface_edge_status: str
    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class SurfaceResidualSeed:
    """An explicit residual: something the lens saw but could not own.

    Residuals are first-class surface facts (fail-loud, never a silent drop).
    The assembler materializes them into ``surface_residual`` nodes.
    """

    residual_kind: str
    source_ref: SourceSpanRef | None
    local_discriminator: str
    rule_id: str
    reason_code: str
    payload: Mapping[str, object]
    residual_status: str = "open"


@dataclass(frozen=True, slots=True)
class SurfaceLensResult:
    """The full output of one lens analysis pass (§D2)."""

    lens_id: str
    node_seeds: tuple[SurfaceNodeSeed, ...]
    edge_seeds: tuple[SurfaceEdgeSeed, ...]
    residuals: tuple[SurfaceResidualSeed, ...]
    diagnostics: tuple[SurfaceDiagnostic, ...]
    coverage: Mapping[str, object]


# ── Lens contract (§D2) ──────────────────────────────────────────────────────


@runtime_checkable
class SurfaceLens(Protocol):
    """A registered surface recognizer (Finland owns the implementations).

    Explicit registration, not hidden global plugins. A lens emits intrinsic
    edges only (edges wholly inside its own local output); cross-lens edges are
    computed by edge passes in the assembler (§D5).
    """

    lens_id: str
    jurisdiction: str
    schema_version: str
    produces_node_kinds: tuple[str, ...]
    produces_edge_kinds: tuple[str, ...]
    required_views: tuple[str, ...]  # "raw_text" in v0; "token_tape"/"morph_overlay" later

    def analyze(
        self,
        bundle: SourceSurfaceBundle,
        *,
        context: SurfaceAnalysisContext,
    ) -> SurfaceLensResult: ...
