"""Shared closed generic-role-actor vocabulary for the Finnish surface lenses.

This module is the SINGLE source of truth for the small CLOSED list of *generic
legal role/class actors* — ``viranomainen``, ``hakija``, ``tuomioistuin``,
``ministeriö``, ``kunta``, ``työnantaja`` … — together with their common
inflected (genitive) and sentence-initial capitalized surface variants. These
are the role classes that head an actor noun phrase in statutory prose
("Viranomainen ei saa …", "hakijan on toimitettava …").

DISTINCT FROM THE INSTITUTIONAL REGISTRY
========================================
This is NOT :data:`lawvm.finland.canonical_actor_registry.REGISTRY`. The
registry carries *named institutions* (specific ministries, agencies, named
government bodies — "Verohallinto", "valtioneuvosto"). This module carries the
*generic role classes* the institutional registry does not (unambiguously)
carry. The surface lenses consume BOTH: registry phrases (read-only) UNION this
closed role list.

CLOSED-LIST DISCIPLINE
======================
The role-actor set is a CLOSED, audited tuple (mirrors ``vague.py`` §1.11). A
surface outside it never matches. New role classes are added by editing this
tuple, never by heuristic. The list was extracted as the UNION of the
previously-duplicated copies in the H4 actor/modal lens, the H5 delegation lens
and the H5 procedure lens, deduplicated and ordered longest-first.

The delegation lens deliberately uses a NARROWER subset of this list (only the
role surfaces that can head a delegation clause), because there the role list
also gates the "treat a registry-ambiguous bare surface as the generic role"
decision in ``_resolve_actor``. That narrowing is expressed explicitly via
:data:`DELEGATION_ROLE_ACTORS` rather than by keeping a separate literal copy.
"""
from __future__ import annotations

from typing import Tuple

#: The canonical CLOSED set of generic role-actor surfaces (nominative + the
#: common genitive variants), as the UNION of the three lens copies, deduped and
#: ordered longest-first. Sentence-initial capitalized variants are added by the
#: lenses via :func:`expand_role_actor_phrases` (the lenses also union in the
#: institutional registry, so capitalization is applied at phrase-build time).
#:
#: Longest-first so a more specific surface ("valvontaviranomainen") is matched
#: before a shorter overlapping one ("viranomainen") in a longest-first
#: alternation.
ROLE_ACTORS: Tuple[str, ...] = tuple(
    sorted(
        {
            "viranomainen",
            "viranomaisen",
            "hakija",
            "hakijan",
            "tuomioistuin",
            "tuomioistuimen",
            "ministeriö",
            "ministeriön",
            "kunta",
            "kunnan",
            "elinkeinonharjoittaja",
            "elinkeinonharjoittajan",
            "työnantaja",
            "työnantajan",
            "työntekijä",
            "työntekijän",
            "asianosainen",
            "asianosaisen",
            "yhtiö",
            "yhtiön",
            "rekisterinpitäjä",
            "rekisterinpitäjän",
            "valvontaviranomainen",
            "valvontaviranomaisen",
            "valittaja",
            "valittajan",
        },
        key=len,
        reverse=True,
    )
)

#: The NARROWER closed subset used by the H5 delegation lens. Only role classes
#: that can head a delegation clause ("ministeriön asetuksella …",
#: "viranomainen antaa määräyksiä …"). Kept as an EXPLICIT subset of
#: :data:`ROLE_ACTORS` (not a separate literal list) so the narrowing is visible
#: and audited. The bare generic "ministeriö" is deliberately treated as the
#: generic role here (it is registered ambiguously across ministries).
DELEGATION_ROLE_ACTORS: Tuple[str, ...] = tuple(
    r
    for r in ROLE_ACTORS
    if r.lower()
    in {
        "ministeriö",
        "ministeriön",
        "viranomainen",
        "viranomaisen",
    }
)


def _capitalize_first(word: str) -> str:
    """Capitalize only the first character (str.capitalize lowercases the rest)."""
    if not word:
        return word
    return word[0].upper() + word[1:]


def expand_role_actor_phrases(roles: Tuple[str, ...]) -> Tuple[str, ...]:
    """Expand a role-actor tuple with sentence-initial capitalized variants.

    Returns each role surface plus its capitalized form ("viranomainen" ->
    {"viranomainen", "Viranomainen"}), deduplicated. Ordering is not significant
    here — callers union these into a larger phrase set and re-sort longest-first.
    """
    expanded: set[str] = set()
    for role in roles:
        expanded.add(role)
        expanded.add(_capitalize_first(role))
    return tuple(expanded)
