"""The SMALL stored flags --- NOT form tables.

These are the entire irreducible "vocabulary" of M1: per-lemma boolean / enum
flags that the rules cannot derive from the surface.  Everything else is rule.

* :data:`SINGLE_K` --- single-``k`` realization (k -> zero/v/j).  Lexically
  conditioned; most are ``zero``.
* :data:`GRADATION_OCCURS` --- the gradation-*occurrence* boolean for lemmas
  whose cluster looks gradating but does not (or vice versa).  Heads carry their
  own flag in :mod:`heads`; this table is for non-head lexemes.
* :data:`AGENCY_ACRONYMS` --- colon-genitive, NOT rule-inflectable (VM -> VM:n).
* :data:`EXTERNAL_LOCATIVE` --- municipalities that take the external locative
  series (-lla/-lta/-lle) as their idiomatic locative.
"""

from __future__ import annotations

# Single-k realization: lemma -> "zero" | "v" | "j".
SINGLE_K: dict[str, str] = {
    "laki": "zero",  # lain
    "Turku": "zero",  # Turun
    "Helsinki": "zero",  # Helsingin handled by nk->ng rule, not single-k
    # NOTE: Helsinki's k is part of the -nk- cluster -> assimilative rule, so it
    # is intentionally NOT given a single_k flag where the rule already fires.
}
# Helsinki's -nk- is rule-handled; drop it to avoid double-application.
del SINGLE_K["Helsinki"]

# Gradation-occurrence for non-head lexemes whose surface is misleading.
GRADATION_OCCURS: dict[str, bool] = {
    "Turku": True,
    "Helsinki": True,
    "Tampere": False,
    "Verohallinto": True,
    "lautakunta": True,
}

# Agency acronyms: colon-genitive, not rule-inflectable.
AGENCY_ACRONYMS: frozenset[str] = frozenset(
    {
        "VM", "STM", "OM", "YM", "UM", "SM", "TEM", "OKM", "PLM", "LVM",
        "MMM", "KKV", "PRH", "MML", "VNK", "THL", "TTL", "STUK",
    },
)

# External-locative municipalities (-lla/-lta/-lle idiomatic locative).
EXTERNAL_LOCATIVE: frozenset[str] = frozenset(
    {
        "Tampere", "Rovaniemi", "Seinäjoki", "Riihimäki", "Nokia", "Vantaa",
        "Kerava", "Tornio", "Imatra", "Jämsä", "Kemi", "Lieksa", "Kuusamo",
        "Sodankylä", "Kittilä", "Inari", "Raisio", "Kaarina", "Kangasala",
        "Lempäälä", "Pirkkala", "Ylöjärvi", "Sastamala", "Valkeakoski",
        "Akaa", "Orivesi", "Mänttä", "Parkano", "Virrat", "Ikaalinen",
        "Hämeenkyrö", "Ruovesi", "Juupajoki", "Kihniö", "Punkalaidun",
        "Urjala", "Vesilahti", "Pälkäne", "Kuhmoinen", "Jämijärvi",
    },
)


def is_acronym(token: str) -> bool:
    """Return True if ``token`` is a known agency acronym."""
    return token in AGENCY_ACRONYMS


def is_external_locative(place: str) -> bool:
    """Return True if ``place`` takes the external-locative series."""
    return place in EXTERNAL_LOCATIVE


__all__ = [
    "AGENCY_ACRONYMS",
    "EXTERNAL_LOCATIVE",
    "GRADATION_OCCURS",
    "SINGLE_K",
    "is_acronym",
    "is_external_locative",
]
