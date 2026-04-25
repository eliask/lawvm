"""Finland-owned UnitRegistry instance derived from the Finland ontology."""

from __future__ import annotations

from lawvm.finland.ontology import to_unit_registry


FINLAND_REGISTRY = to_unit_registry()


__all__ = ["FINLAND_REGISTRY"]
