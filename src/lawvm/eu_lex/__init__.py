"""EU/treaty boundary scaffold for the Legal Surface Graph (FEASIBILITY-FIRST).

This subpackage is a *scaffold*, not an ingestion path. It de-risks the
cross-jurisdiction boundary by proving that an EUR-Lex-shaped document can be
mapped into the SAME ``SourceSurfaceBundle`` substrate the Finnish pipeline
uses, and that the node id minted for an ingested EU act ALIGNS with the
``entity:celex:<CELEX>`` frontier node the FI reference resolver already mints
for EU targets (see ``finland/references/resolve.py::_resolve_eu_nickname`` and
``finland/legal_surface/corpus_graph.py``).

No network, no real corpus. The synthetic fragment under
:mod:`lawvm.eu_lex.bundle` proves SHAPE alignment only — it does NOT make the
transitive closure cross the jurisdiction boundary. That requires real EU/treaty
text to be ingested (see ``notes/EU_LEX_BOUNDARY_FEASIBILITY.md``).

Placement note: this lives at ``src/lawvm/eu_lex/`` per the lane's ownership
boundary. The natural long-term home, once ingestion is real, is
``src/lawvm/eu/legal_surface/`` — mirroring ``finland/legal_surface/`` and
keeping it beside the existing on-demand Cellar/IR EU frontend in
``src/lawvm/eu/``. That fold is a follow-up, not part of this scaffold.
"""
from __future__ import annotations

from lawvm.eu_lex.bundle import (
    EuActDocument,
    build_eu_surface_bundle,
    parse_eu_act_fragment,
)
from lawvm.eu_lex.celex import (
    celex_to_canonical_id,
    celex_to_entity_id,
    is_well_formed_celex,
)

__all__ = [
    "EuActDocument",
    "build_eu_surface_bundle",
    "celex_to_canonical_id",
    "celex_to_entity_id",
    "is_well_formed_celex",
    "parse_eu_act_fragment",
]
