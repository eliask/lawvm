"""CELEX -> entity-node-id join (the cross-jurisdiction boundary keystone).

The FI reference resolver already mints a target id for an EU act as the
canonical id ``celex:<CELEX>`` (``finland/references/resolve.py``::
``_resolve_eu_nickname``: ``f"celex:{celex}"``), which the core assembler then
lifts to the entity node id ``entity:celex:<CELEX>`` via
``mint_entity_node_id`` (``core/legal_surface_assembler.py``). That
``entity:celex:<CELEX>`` node is today a FRONTIER node: it is reached by an FI
``refers_to`` edge but has no outbound references in the corpus graph, so the
closure stops there.

For a future ingested EU act to make the closure CROSS that boundary, the EU
act's own surface graph must mint its work-entity node under the SAME id. This
module is the single source of truth for that id, so the EU side and the FI
side provably agree.

Reference (verbatim, ``finland/references/resolve.py``)::

    candidate_ids = tuple(f"celex:{celex}" for celex in result.candidates)

and (``core/legal_surface_assembler.py``)::

    def mint_entity_node_id(canonical_id: str) -> str:
        return f"{ENTITY_ID_PREFIX}{canonical_id}"   # ENTITY_ID_PREFIX = "entity:"

So: raw CELEX ``32016R0679`` -> canonical ``celex:32016R0679`` -> node id
``entity:celex:32016R0679``. The raw CELEX is used verbatim (NOT lowercased):
the registry stores upper-case sector/type letters and the FI side does no case
normalization, so neither does this helper.
"""
from __future__ import annotations

import re

# Imported from the core assembler so the prefix can NEVER drift out of sync
# with the FI side. If the assembler renames the prefix, this import breaks
# loudly rather than silently minting a non-aligning id.
from lawvm.core.legal_surface_assembler import mint_entity_node_id

# The canonical-id prefix the FI EU resolver uses for an EU act. Kept as a named
# constant so the join point is greppable from both sides.
CELEX_CANONICAL_PREFIX = "celex:"

# CELEX number structure, documented form ``N<YYYY><SECTOR-LETTER><NNNN...>``.
#   * sector digit (1=treaties, 3=legislation, 6=case law, ...). EU *acts* we
#     resolve via nicknames are sector 3 (legislation), but the boundary is not
#     restricted to 3 — a treaty ingested via CELEX would be sector 1. The
#     regex therefore accepts the documented structural shape, not a hardcoded
#     sector, and callers decide which sectors they ingest.
#   * 4-digit year.
#   * one descriptor letter (R=regulation, L=directive, D=decision, ...).
#   * a numeric tail (commonly 4 digits, but longer/zero-padded forms exist).
# Structurally: one sector digit, then a 4-digit year, then a descriptor
# letter, then the numeric tail (``3`` ``2016`` ``R`` ``0679`` for the GDPR).
# This is a WELL-FORMEDNESS check, not a validity check: a CELEX can be
# well-formed and still not exist. Marked uncertain points are in the design
# note; the helper deliberately stays permissive on the tail length.
_CELEX_RE = re.compile(r"^[1-9]\d{4}[A-Z]\d+$")


def is_well_formed_celex(celex: str) -> bool:
    """True iff ``celex`` matches the documented CELEX number structure.

    Structural only: well-formed != exists. Fail-loud callers should treat a
    ``False`` here as "this is not a CELEX id" and refuse to mint, rather than
    fabricating a node id from a malformed input.
    """
    return bool(_CELEX_RE.match(celex))


def _require_well_formed(celex: str) -> None:
    if not is_well_formed_celex(celex):
        raise ValueError(
            f"not a well-formed CELEX id: {celex!r}; expected "
            r"N<YYYY><LETTER><digits> e.g. '32016R0679'. "
            "Refusing to mint a non-aligning node id from malformed input."
        )


def celex_to_canonical_id(celex: str) -> str:
    """Raw CELEX -> the canonical id the FI side uses (``celex:<CELEX>``).

    This is the *pre-entity* id (what a recognizer/resolver carries as a
    target). The entity node id is :func:`celex_to_entity_id`.
    """
    _require_well_formed(celex)
    return f"{CELEX_CANONICAL_PREFIX}{celex}"


def celex_to_entity_id(celex: str) -> str:
    """Raw CELEX -> the entity node id the corpus graph mints for EU targets.

    Aligns with the FI frontier node: ``32016R0679`` -> ``entity:celex:32016R0679``.
    Routes through the SAME ``mint_entity_node_id`` the assembler uses, so the
    two sides are byte-identical by construction.
    """
    return mint_entity_node_id(celex_to_canonical_id(celex))
