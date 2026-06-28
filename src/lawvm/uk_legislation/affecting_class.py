"""UK affecting-class slug derivation: the single source of truth.

Centralises the ``cls -> slug`` lookup previously duplicated inline at
``effects.UKEffectRecord.affecting_act_id`` and ``tools.uk_cross_statute_graph.
_affected_statute_id``. Both produced the invalid slug ``cls.lower()`` for any
class absent from the map (e.g. ``NorthernIrelandAct`` -> ``northernirelandact``),
which 404s at archive fetch and reads to a human as a generic missing-XML error
(AGENTS.md §1.10 literal DON'T example). The helper raises a typed
``UnmappedAffectingClass`` so the concrete fix ("add a class-to-slug mapping, or
supply a usable AffectingURI") surfaces loudly at the slug-derivation boundary
rather than as a downstream soft 404.

The shared helper is the missing abstraction called for in AGENTS.md §2.6: the
same fix shape landed twice (the literal ``_UK_AFFECTING_CLASS_SLUG_MAP.get(cls,
cls.lower())`` is identical at both sites), so it is crystallised here before a
third site appears.
"""
from __future__ import annotations

import re
from typing import Optional


# Maps an effects-feed AffectingClass to its legislation.gov.uk document-type slug.
# Necessarily incomplete: legislation.gov.uk has many types, so the effect
# AffectingURI is the authoritative source and the class map is a fallback only.
# A class absent here, with no usable URI, raises ``UnmappedAffectingClass`` so
# the missing mapping surfaces loudly (``uk_affecting_act_class_unmapped_rejected``)
# rather than silently producing the invalid ``cls.lower()`` slug.
_UK_AFFECTING_CLASS_SLUG_MAP: dict[str, str] = {
    "UnitedKingdomPublicGeneralAct": "ukpga",
    "UnitedKingdomStatutoryInstrument": "uksi",
    "WelshParliamentAct": "asc",
    "WelshStatutoryInstrument": "wsi",
    "ScottishAct": "asp",
    "ScottishStatutoryInstrument": "ssi",
    "NorthernIrelandAssemblyMeasure": "mnia",
    "NorthernIrelandParliamentAct": "apni",
    "NorthernIrelandStatutoryRule": "nisr",
    "UnitedKingdomChurchInstrument": "ukci",
    "UnitedKingdomMinisterialOrder": "ukmo",
    "EuropeanUnionRegulation": "eur",
    "EuropeanUnionDecision": "eudn",
    "EuropeanUnionDirective": "eudr",
}


# Matches the slug/year/number triple in a legislation.gov.uk URI, e.g.
# ``https://www.legislation.gov.uk/id/ukpga/2023/28`` -> ("ukpga", "2023", "28").
# Used to recognise the authoritative slug when an AffectingURI is available
# (the class map is incomplete; the URI is authoritative).
_UK_AFFECTING_URI_SLUG_RE = re.compile(
    r"legislation\.gov\.uk/(?:id/)?([a-z]{1,16})/(\d{1,9})/(\d{1,9})\b"
)


# Default hint text — mirrors AGENTS.md §1.10 DO example wording.
_UNMAPPED_AFFECTING_CLASS_HINT = (
    "add an entry to _UK_AFFECTING_CLASS_SLUG_MAP for this class, or supply "
    "a usable AffectingURI on the effect record"
)


class UnmappedAffectingClass(ValueError):
    """A UK affecting-class string has no document-type slug mapping.

    Distinct, named diagnostic per AGENTS.md §1.10: the prior behaviour returned
    ``cls.lower()`` for any unmapped class, producing an invalid slug (e.g.
    ``northernirelandact``) that 404s at archive fetch and reads to a human as a
    generic missing-XML error. The exception names the concrete fix (add a
    class-to-slug mapping, or supply a usable AffectingURI) and carries the
    offending class, year, and number so the residual is self-evidencing —
    triaging it must never require re-running extraction.

    Mirrors the field-carrying pattern used elsewhere (``FixedTermDiagnostic``'s
    ``clause_text``, ``UnmappedSpanAnchorKindError``'s embedded-kind message),
    kept as a raised exception per the AGENTS.md §1.10 DO example
    (``raise UnmappedAffectingClass(cls=cls)``). The discriminator-plus-fields
    shape lets callers surface the residual loudly via the typed finding
    emission (``uk_affecting_act_class_unmapped_rejection``) rather than as a
    soft 404.

    Note: the constructor keyword is ``class_name=`` (not ``cls=``) — the
    implicit ``cls`` parameter of ``BaseException.__new__`` shadows a same-name
    field, and a strict type checker flags ``cls=cls`` as ambiguous. The
    semantic field name (``cls`` as a class string) is preserved on the read
    side via the ``.cls`` attribute alias below.
    """

    def __init__(
        self,
        *,
        class_name: str,
        year: Optional[str] = None,
        number: Optional[str] = None,
        hint: str = _UNMAPPED_AFFECTING_CLASS_HINT,
    ) -> None:
        super().__init__(
            f"unmapped UK affecting-class {class_name!r} "
            f"(year={year!r}, number={number!r}): {hint}"
        )
        # Public attribute stays ``cls`` to match the AGENTS.md §1.10 DO example
        # (``raise UnmappedAffectingClass(cls=cls)``) on the read side; the
        # constructor keyword is ``class_name=`` to satisfy the type checker.
        self.cls = class_name
        self.class_name = class_name
        self.year = year
        self.number = number
        self.hint = hint


def affecting_class_slug(
    cls: str,
    *,
    year: Optional[str],
    number: Optional[str],
) -> str:
    """Return the document-type slug for ``cls``, or raise ``UnmappedAffectingClass``.

    The slug is the legislation.gov.uk document-type prefix (``ukpga``, ``uksi``,
    ``nia`` etc.) used to construct ``{slug}/{year}/{number}`` affecting-act ids
    and edge target ids. For an unmapped class with no usable URI, the prior
    behaviour was ``cls.lower()`` — a guessed slug that 404s at archive fetch
    and reads to a human as a generic missing-XML error (AGENTS.md §1.10).
    The helper raises so callers surface the missing mapping loudly via the
    typed ``UnmappedAffectingClass`` diagnostic instead.

    Callers that need a soft backward-compat slug (witness attribution for an
    effect still in flight before its rejection is emitted) should check
    ``is_affecting_class_recognized(cls=..., uri=...)`` first and surface the
    unmapped case via the ``uk_affecting_act_class_unmapped_rejection`` finding
    rather than letting the exception propagate.
    """
    slug = _UK_AFFECTING_CLASS_SLUG_MAP.get(cls)
    if slug is None:
        raise UnmappedAffectingClass(class_name=cls, year=year, number=number)
    return slug


def is_affecting_class_recognized(*, cls: str, uri: str) -> bool:
    """True when the affecting-act slug is authoritative (URI or class map).

    The predicate gates access to slug-derivation: a class not recognised by
    either source would raise ``UnmappedAffectingClass`` from
    ``affecting_class_slug``. Callers that need the soft-fall-through behaviour
    should check this predicate first and route the unmatched case through the
    ``uk_affecting_act_class_unmapped_rejection`` finding, instead of letting
    the helper raise. Equivalent to ``UKEffectRecord.affecting_class_is_recognized``
    and centralised here so the recognition verdict and the slug-derivation
    verdict cannot drift apart.
    """
    if _UK_AFFECTING_URI_SLUG_RE.search(str(uri or "")):
        return True
    return str(cls or "") in _UK_AFFECTING_CLASS_SLUG_MAP


__all__ = [
    "UnmappedAffectingClass",
    "_UK_AFFECTING_CLASS_SLUG_MAP",
    "_UK_AFFECTING_URI_SLUG_RE",
    "affecting_class_slug",
    "is_affecting_class_recognized",
]
