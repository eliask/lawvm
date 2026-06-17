"""Referent registries (Index B) --- surface -> referent-id resolution.

Each registry maps a (possibly inflected) referent surface to the canonical
identity it names.  Built generation-first from the morphology engine (M1): a
small set of canonical entries is expanded into its inflected surface variants,
so that an inflected citation (``Holhouslain``, ``Ulosottolaissa``) resolves
without storing form tables.  On a miss or multi-hit each registry returns a
typed status and (for the caller) a finding rather than a silent guess --- the
§6 *registry / convention dependencies* of the FI Reference Catalogue.

Hosts:
  - ``statute_name`` --- Finnish statute title -> statute id (temporal).
  - ``eu_nickname``  --- Finnish EU-instrument nickname -> CELEX.

The statute-name API is re-exported flat below.  ``eu_nickname`` is accessed
via its submodule path (``from ...registries import eu_nickname``) because both
registries define a ``RegistryResult`` of their own shape.

See ``notes_internal/FI_MORPHOLOGY_DESIGN_DECISION.md`` (Index B) and
``FI_PARSE_OVERLAY_IR_MODEL.md``.
"""

from __future__ import annotations

from .statute_name import (
    RegistryResult,
    StatuteNameEntry,
    StatuteNameRegistry,
    build_registry,
    default_artifact_path,
    load_statute_name_entries,
    load_statute_name_registry,
    sample_entries_from_farchive,
    serialize_entries,
)

__all__ = [
    "RegistryResult",
    "StatuteNameEntry",
    "StatuteNameRegistry",
    "build_registry",
    "default_artifact_path",
    "load_statute_name_entries",
    "load_statute_name_registry",
    "sample_entries_from_farchive",
    "serialize_entries",
]
