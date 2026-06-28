"""Typed ``ScopeConfidence`` carrier protocol for the ``LegalOperation`` boundary.

``ScopeConfidence`` is the typed marker for the frontend-owned rider stored on
``LegalOperation.scope_confidence`` (formerly ``Any``). Core stores the carrier
but does NOT interpret jurisdiction-local fields (AGENTS.md §2.3 "core does not
interpret frontend-local values"). Promoting the field from ``Any`` to
``Optional[ScopeConfidence]`` makes bare-string smuggling a type error at the
core semantic boundary (AGENTS.md §1.9 — typed carriers over dynamic shape),
which is the §1.10 fail-loud endpoint: a frontend that hands a free-form
string to ``LegalOperation.scope_confidence`` raises
``UnregisteredScopeConfidence`` instead of silently cohabiting with the typed
dataclass instances another frontend already writes.

The protocol is intentionally a marker (no required members). It exists to
gate the ``LegalOperation`` boundary against bare ``str`` values, not to
impose a one-shape-fits-all dataclass layout: each frontend keeps its own
``ScopeConfidence``/``NOScopeConfidence``/... dataclass with the
jurisdiction-local fields it already carries, and explicitly inherits this
protocol so the AST scan in ``tests/test_scope_confidence_protocol.py`` can
verify producer-set == protocol-implementer-set. Frontends SHOULD also surface
the AGENTS.md §2.2 ``rung_id`` vocabulary (``explicit_source``,
``explicit_source_with_context``, ``inferred_from_group``,
``inferred_from_payload``, ``inferred_from_live_unique``, ``fallback``) for
cross-jurisdiction comparability, but core does not branch on that value
today — anything a frontend needs core to act on must cross its own typed
waist, not ride on this marker.

Mirrors the ``coerce_recovery_kind`` / ``coerce_quirks_disposition`` precedent
(AGENTS.md §1.10 fail-loud, AGENTS.md §2.1 stable rule ID). Unlike those, this
is a marker protocol for an opaque carrier, not a closed ``StrEnum``: a
free-form string is rejected because it is not a typed instance, not because
it is not a registered member of a closed vocabulary.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScopeConfidence(Protocol):
    """Marker protocol for the frontend-owned typed ``scope_confidence`` rider.

    Stored on ``LegalOperation.scope_confidence``. Core does NOT interpret
    jurisdiction-local fields; the protocol is a typed boundary so bare-string
    smuggling fails loud at the ``LegalOperation.__post_init__`` waist.

    Inheriting this protocol explicitly:
      * is structurally a no-op (the protocol has no required members), AND
      * registers the frontend dataclass as a producer in the AST-scan parity
        check (``tests/test_scope_confidence_protocol.py``), keeping the
        producer set (frontend dataclasses that emit instances) equal to the
        protocol-implementer set (frontend dataclasses that inherit).

    Frontends SHOULD additionally expose a ``rung_id`` property emitting the
    AGENTS.md §2.2 vocabulary so a future core consumer can read a stable
    cross-jurisdiction ladder value; core does not branch on it today.
    """

    pass  # Marker protocol: any typed instance structurally conforms.


class UnregisteredScopeConfidence(TypeError):
    """A ``LegalOperation.scope_confidence`` value is not a typed instance.

    Raised at the ``LegalOperation.__post_init__`` boundary instead of silently
    accepting a bare string alongside a frontend's typed ``ScopeConfidence``
    dataclass. The fix is always "construct a frontend-local ``ScopeConfidence``
    dataclass (inheriting ``lawvm.core.scope_confidence.ScopeConfidence``) and
    pass that instance instead of a free-form string" — keeping the
    ``LegalOperation.scope_confidence`` waist typed (AGENTS.md §1.9) and the
    §1.10 fail-loud contract intact.
    """

    def __init__(self, value: ScopeConfidence | str | None) -> None:
        self.value = value
        super().__init__(
            f"LegalOperation.scope_confidence must be a typed ScopeConfidence "
            f"instance, got {type(value).__name__}: {value!r}; construct a "
            f"frontend-local ScopeConfidence dataclass inheriting "
            f"lawvm.core.scope_confidence.ScopeConfidence and pass that instead"
        )


def coerce_scope_confidence(value: ScopeConfidence | str | None) -> ScopeConfidence | None:
    """Coerce a stored value to a typed ``ScopeConfidence`` instance, failing loud.

    Used at the ``LegalOperation.__post_init__`` boundary (the semantic core
    waist) so a bare ``str`` cannot ride alongside a frontend's typed
    ``ScopeConfidence`` dataclass. A non-``None`` string is always a
    registration gap, never a silent fallback: raise
    ``UnregisteredScopeConfidence``. ``None`` is the legitimate "no witness"
    sentinel and is passed through unchanged.

    A non-string, non-``None`` value is presumed to be a frontend-local typed
    instance that structurally conforms to the marker ``ScopeConfidence``
    protocol. Core does NOT inspect jurisdiction-local fields here (AGENTS.md
    §2.3 "core does not interpret frontend-local values"); structural protocol
    membership is sufficient at this boundary.
    """
    if value is None:
        return None
    if isinstance(value, str):
        raise UnregisteredScopeConfidence(value)
    return value


__all__ = [
    "ScopeConfidence",
    "UnregisteredScopeConfidence",
    "coerce_scope_confidence",
]
