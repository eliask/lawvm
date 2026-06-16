"""UK transition-graph export profile.

UK statute ids are already in the canonical legislation.gov.uk path form
(``ukpga/1998/42``, ``nia/2010/13``, ``asp/2012/8``, ``uksi/2013/123`` ...), so
the canonical and engine codecs are normalising identities and the source URLs
are the plain legislation.gov.uk document URLs.
"""
from __future__ import annotations

from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile

_LEG_BASE = "https://www.legislation.gov.uk"


def uk_canonical_statute_id(statute_id: str) -> str:
    return str(statute_id or "").strip().strip("/")


def uk_engine_statute_id(statute_id: str) -> str:
    return uk_canonical_statute_id(statute_id)


def uk_statute_url(_canonical_id: str, engine_id: str) -> str:
    engine_id = uk_canonical_statute_id(engine_id)
    if not engine_id:
        return ""
    return f"{_LEG_BASE}/{engine_id}"


def uk_amendment_url(_canonical_id: str, engine_id: str) -> str:
    return uk_statute_url(_canonical_id, engine_id)


def uk_transition_graph_export_profile() -> TransitionGraphExportProfile:
    return TransitionGraphExportProfile(
        jurisdiction="uk",
        lang="en",
        canonical_statute_id=uk_canonical_statute_id,
        engine_statute_id=uk_engine_statute_id,
        statute_url=uk_statute_url,
        amendment_url=uk_amendment_url,
    )
