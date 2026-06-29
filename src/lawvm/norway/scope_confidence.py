"""Norway-local typed ``ScopeConfidence`` carrier for the ``LegalOperation`` boundary.

Norway producers historically handed bare §2.2 ladder strings
(``"inferred_from_payload"``, ``"explicit_source_with_context"``, ``"fallback"``)
into the parse-recovery adjudication and adjudication-detail surface; the
``LegalOperation.scope_confidence: Any`` field let such bare strings cohabit
with Finland's typed dataclass at the core semantic boundary, which is the
silent-failure smell AGENTS.md §1.9 (typed carriers over dynamic shape) and
§1.10 (fail loud, never silent-fallback) warn against.

``NOScopeConfidence`` is the typed instance Norway now passes in place of those
bare strings. It inherits the marker ``lawvm.core.scope_confidence.ScopeConfidence``
protocol so the AST-scan parity check in
``tests/test_scope_confidence_protocol.py`` keeps producer-set ==
protocol-implementer-set across frontends. Carrying the rung as a typed
instance means it cannot be silently smuggled past the
``LegalOperation.__post_init__`` waist — a bare string raises
``UnregisteredScopeConfidence`` there.

Mirrors the Finland ``ScopeConfidence`` shape but stays Norway-shaped:
Norway producers emit the §2.2 rung directly (the source-parse recovery path
already knows whether it inferred-from-payload or recovered-from-lead-with-context),
rather than the rich Finland-local ``ScopeResolutionSource`` /
``ScopeResolutionConfidence`` enum pair.
"""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.core.scope_confidence import ScopeConfidence as _CoreScopeConfidenceProtocol
from lawvm.core.target_resolution import (
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
    SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
    SCOPE_CONFIDENCE_FALLBACK,
    SCOPE_CONFIDENCE_INFERRED_FROM_GROUP,
    SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE,
    SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD,
)


# Closed §2.2 ladder vocabulary (AGENTS.md §2.2 "track how it was obtained").
# Reuses the canonical constants from ``lawvm.core.target_resolution`` so a
# Norway rung and a ``TargetResolutionCoverage.scope_confidence`` rung are
# the same checkable string. Adding a new Norway rung requires extending this
# frozenset; an unregistered rung fails loud at ``__post_init__``.
_VALID_NO_SCOPE_CONFIDENCE_RUNGS: frozenset[str] = frozenset(
    {
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE,
        SCOPE_CONFIDENCE_EXPLICIT_SOURCE_WITH_CONTEXT,
        SCOPE_CONFIDENCE_INFERRED_FROM_GROUP,
        SCOPE_CONFIDENCE_INFERRED_FROM_PAYLOAD,
        SCOPE_CONFIDENCE_INFERRED_FROM_LIVE_UNIQUE,
        SCOPE_CONFIDENCE_FALLBACK,
    }
)


@dataclass(frozen=True, slots=True)
class NOScopeConfidence(_CoreScopeConfidenceProtocol):
    """Norway-local typed witness for scope-resolution provenance.

    Stores the §2.2 ladder rung directly (``"inferred_from_payload"``,
    ``"explicit_source_with_context"``, ...). The rung MUST be one of the
    canonical ``SCOPE_CONFIDENCE_*`` constants; an unregistered value fails
    loud at ``__post_init__`` (AGENTS.md §1.10 — a missing mapping cannot
    fall back silently to ``cls.lower()`` or a guess).
    """

    rung_id: str

    def __post_init__(self) -> None:
        if self.rung_id not in _VALID_NO_SCOPE_CONFIDENCE_RUNGS:
            raise ValueError(
                f"NOScopeConfidence.rung_id must be one of "
                f"{sorted(_VALID_NO_SCOPE_CONFIDENCE_RUNGS)}; "
                f"got {self.rung_id!r}"
            )


__all__ = ["NOScopeConfidence"]
