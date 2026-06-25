"""Typed Finland op-provenance / acceptance-mode model.

This module owns the canonical typed form for *how a compiled op was derived*
and *whether a strict consumer may accept it*. It is the consolidation target
for the scattered provenance/recovery primitives currently spread across
``AmendmentOp`` (the ``*_fallback`` booleans and ``*_provenance_tags`` string
bags) and the stringly ``quirks_disposition``/``strict_disposition`` finding
metadata.

Design intent (see ``notes/FI_OP_PROVENANCE_CONSOLIDATION_SPEC.md``):

- ``OpProvenance`` is a sum type: ``Parsed`` (a grammar rule produced the op) or
  ``Recovered`` (a recognizer/fallback guessed it). Recognizer coverage is
  *intrinsic* to ``Recovered`` — there is no separate "with coverage" shadow.
- ``ConfidenceTier`` is a DISCRETE enum (no floats, no numeric thresholds),
  mirroring the existing ``CiteConfidence`` / ``ScopeResolutionConfidence``
  style: string values + semantic docstrings.
- ``AcceptanceMode`` is keyed on the provenance: ``STRICT`` admits only
  ``Parsed`` ops; ``QUIRKS`` records-with-finding. This makes "silently relying
  on a guess in strict mode" a type-level impossibility for any consumer that
  routes acceptance through :func:`admits` / :func:`mode_for`.

``AcceptanceMode`` is DERIVED FROM the existing :class:`StrictProfile`
(``lawvm.core.compile_result``), never a second toggle: :func:`mode_for` is the
only bridge between the two. ``StrictProfile`` remains the single source of
truth for strict-vs-quirks policy.

This module is intentionally dependency-light: it does not import from
``lawvm.finland.ops`` (so it can be wired into ``AmendmentOp`` in a later phase
without an import cycle). The scope-confidence facet of ``Recovered`` is typed
under ``TYPE_CHECKING`` only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lawvm.core.compile_result import StrictProfile
    from lawvm.finland.ops import ScopeConfidence


class RecoverySurface(Enum):
    """Which surface a recovery recognizer read to guess the op."""

    BODY = "body"
    """Johtolause body-text recovery (the rank-3 fallback heuristic)."""

    TITLE = "title"
    """Title-only recovery; weakest surface by construction."""

    SCOPE = "scope"
    """Chapter-scope resolution recovery."""

    PAYLOAD = "payload"
    """Sparse-omission / payload elaboration recovery."""


class ConfidenceTier(Enum):
    """Discrete recovery confidence, ordered worst -> best.

    No floats and no numeric thresholds: a tier is assigned by *which
    recognizer* produced the op, never by scoring. This matches the project's
    other confidence enums (``CiteConfidence``, ``ScopeResolutionConfidence``).
    """

    TITLE_ONLY = "title_only"
    """Recovered from the act title alone; the body yielded no ops."""

    HEURISTIC = "heuristic"
    """Bare body-text regex heuristic; no span-coverage witness."""

    COVERAGE_BACKED = "coverage_backed"
    """Body heuristic carrying its intrinsic recognizer span coverage."""

    ANCHORED = "anchored"
    """Context-resolved against live structure (strongest recovery)."""


@dataclass(frozen=True, slots=True)
class RecognitionCoverage:
    """Recognizer span coverage, intrinsic to a ``Recovered`` provenance.

    Folds in the diagnostics that
    ``normalize.parse_ops_fallback_heuristic_with_coverage`` returns separately
    today: which input spans the bounded recognizers covered, and which they
    skipped (still-unowned source text).
    """

    recognized_spans: tuple[tuple[int, int], ...] = ()
    skipped_spans: tuple[tuple[int, int], ...] = ()

    @property
    def is_total(self) -> bool:
        """True when the recognizer left no skipped span unowned."""
        return not self.skipped_spans


@dataclass(frozen=True, slots=True)
class Parsed:
    """The op was produced by a deterministic grammar rule (not a guess)."""

    grammar_rule_id: str


@dataclass(frozen=True, slots=True)
class Recovered:
    """The op was guessed by a recovery recognizer / fallback.

    ``recognizer_id`` subsumes the diagnostic ``witness_rule_id`` and the
    load-bearing provenance tag strings: it names the recognizer (in the
    ``recovery_authorization_registry`` ``kind`` namespace) that produced the
    op, so a strict consumer can ask the registry whether to block it.
    """

    surface: RecoverySurface
    recognizer_id: str
    tier: ConfidenceTier
    coverage: RecognitionCoverage = field(default_factory=RecognitionCoverage)
    scope_confidence: "ScopeConfidence | None" = None


OpProvenance = Parsed | Recovered
"""Sum type carried (eventually) by every compiled op."""


class AcceptanceMode(Enum):
    """Whether a consumer accepts recovered (guessed) ops."""

    STRICT = "strict"
    """Rejects any ``Recovered`` op; admits only ``Parsed``."""

    QUIRKS = "quirks"
    """Records-with-finding; admits all provenance."""


def admits(mode: AcceptanceMode, provenance: OpProvenance) -> bool:
    """Return whether ``mode`` accepts an op with ``provenance``.

    STRICT admits only :class:`Parsed`. This is the type-level guard: a strict
    consumer that routes acceptance through this function cannot silently
    execute a guessed (:class:`Recovered`) op.
    """
    if mode is AcceptanceMode.QUIRKS:
        return True
    return isinstance(provenance, Parsed)


def mode_for(profile: "StrictProfile | None", provenance: OpProvenance) -> AcceptanceMode:
    """Derive the acceptance mode for ``provenance`` under ``profile``.

    ``StrictProfile`` is the single source of truth. ``None`` means lenient
    (QUIRKS). A non-None profile yields STRICT for the recovery surface(s) it
    forbids and QUIRKS otherwise, keyed per-recovery so the per-family
    ``allows_*`` booleans stay authoritative.

    A :class:`Parsed` op is never recovered, so it is always QUIRKS-equivalent
    (admitted everywhere); the surface gate only matters for :class:`Recovered`.
    """
    if profile is None:
        return AcceptanceMode.QUIRKS
    if isinstance(provenance, Parsed):
        return AcceptanceMode.QUIRKS

    surface = provenance.surface
    if surface is RecoverySurface.BODY or surface is RecoverySurface.TITLE:
        # Body/title recovery is target-guessing in the StrictProfile sense.
        forbidden = not profile.allows_target_guessing
    elif surface is RecoverySurface.SCOPE:
        forbidden = not profile.allows_context_dependent_anchor_resolution
    elif surface is RecoverySurface.PAYLOAD:
        forbidden = not profile.allows_omission_expansion
    else:  # pragma: no cover - exhaustive over RecoverySurface
        raise ValueError(f"unhandled RecoverySurface: {surface!r}")

    return AcceptanceMode.STRICT if forbidden else AcceptanceMode.QUIRKS


__all__ = [
    "AcceptanceMode",
    "ConfidenceTier",
    "OpProvenance",
    "Parsed",
    "RecognitionCoverage",
    "Recovered",
    "RecoverySurface",
    "admits",
    "mode_for",
]
