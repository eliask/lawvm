"""Abstract typed primitive protocol for pool / budget-line / quantity mentions.

Core owns ONLY the marker protocol that frontend concrete dataclasses inherit.
The concrete Finnish primitive lives in
``lawvm.finland.pool_mention_primitive`` (AGENTS.md §2.3 -- jurisdiction-local
drafting idioms live in the frontend, not in core).

Mirrors the ``ScopeConfidence`` precedent (``lawvm.core.scope_confidence``): a
marker protocol with no required members gates the boundary against free-form
``str`` values; each frontend keeps its own concrete dataclass with the
jurisdiction-local fields it carries, and explicitly inherits this protocol so
the AST-scan parity check in
``tests/test_core_firewall_no_fi_fiscal_doctrine.py`` can verify that no
Finnish fiscal-doctrine literals (the doctrine this protocol abstracts over)
ever enter ``src/lawvm/core/**/*.py``. Core does NOT interpret frontend-local
fields (AGENTS.md §2.3); the protocol is a typed boundary, not a vocabulary.

This module has no Finland-specific imports. Concrete Finland extraction and
projection live in ``lawvm.finland.pool_mention_extractor`` /
``lawvm.finland.pool_mention_primitive``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProvisionMention(Protocol):
    """Marker protocol for the frontend-owned typed pool/quantity mention rider.

    A frontend's concrete PoolMention dataclass (e.g.
    ``lawvm.finland.pool_mention_primitive.PoolMention``) explicitly inherits
    this protocol so:
      * it is structurally a no-op (the protocol has no required members), AND
      * it registers the frontend dataclass as a producer in the AST-scan
        parity check (``tests/test_core_firewall_no_fi_fiscal_doctrine.py``),
        keeping the producer set (frontend dataclasses that emit instances)
        equal to the protocol-implementer set (frontend dataclasses that
        inherit).

    Core does NOT interpret jurisdiction-local fields; the protocol exists to
    keep the core/frontend firewall structural rather than rhetorical. Anything
    a frontend needs core to act on must cross its own typed waist, not ride on
    this marker.
    """

    pass  # Marker protocol: any typed instance structurally conforms.


__all__ = [
    "ProvisionMention",
]
