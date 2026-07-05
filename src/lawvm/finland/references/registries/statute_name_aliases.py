"""Curated colloquial-nickname -> statute-id alias table (Index B recall lever).

A large share of by-name statute citations end UNRESOLVED (``statute_only``)
because the act is *cited* by a colloquial abbreviation that its *official title*
does not contain, so the registry's generation-first inflection (which only ever
produces surfaces derivable from the canonical title) can never emit the cited
key.  The dominant pattern (``tools.resolution_miss_analysis`` bucket (a)/(d)):

    official title  "Laki viranomaisten toiminnan julkisuudesta"  (1999/621)
    cited as        "julkisuuslaissa" -> normalized key ``julkisuuslaki``

``julkisuuslaki`` is a real, universally-understood nickname but it is NOT
morphologically derivable from the title — morphology was tried and correctly
defers it (a WRONG reverse-derivation is worse than a miss).  The remaining lever
is a small CURATED table of confidently-mapped colloquial nicknames.  Because it
is curated (a human-verified judgement, not a pure function of the corpus) it is
COMMITTED, unlike the regenerable full registry artifact.

Inclusion discipline (every entry satisfies ALL of these):

* **Corpus-verified single identity.**  The target id's ``docTitle`` was read
  from the farchive and the nickname maps to EXACTLY ONE base act.  A nickname
  whose referent changed over time (an act repealed and re-enacted under a
  *renamed* title both colloquially carrying the nickname) is EXCLUDED — it would
  force a silent temporal pick, which the fail-loud registry must never make.
  (E.g. ``kuntalaki`` 1995/365 vs 2015/410 is deliberately left to the registry's
  own ``multiple`` outcome — both acts ARE titled "Kuntalaki", so generation
  already indexes them and resolution is correctly ambiguous.)

* **Nickname differs from the indexed title.**  An act whose nominative title IS
  the nickname (``Metsälaki`` -> ``metsälaki``) is already indexed by generation
  and is NOT an alias here; including it would be redundant and risk shadowing a
  legitimately-ambiguous generated surface.  Every key below was confirmed to
  currently return ``none`` from the generated registry.

* **No unverified guess.**  Only nicknames whose official act could be located
  in the corpus are listed.  A plausible-but-unconfirmed nickname is omitted.

Each entry carries the official title it abbreviates as provenance.

The mapping is wired into the registry by :func:`build_registry`
(``statute_name.py``): each alias is registered as another surface key for its
target id, coexisting with the generated/derived surfaces.  If a curated alias
key were ever to collide with a DIFFERENT id already indexed under that key, the
registry lands ``status="multiple"`` (the safe fail-loud outcome) — an alias is
never a silent override.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StatuteNameAlias:
    """One curated colloquial nickname binding.

    Attributes:
        alias_key:   the normalized nickname key (lower-case, single token) as
            the by-name recognizer produces it (``modifier + nominative head``).
        statute_id:  the ``NNN/YYYY`` id of the single base act it names.
        official_title: the act's official ``docTitle`` (provenance — the title
            the nickname abbreviates; carried as the candidate display title).
    """

    alias_key: str
    statute_id: str
    official_title: str


# Curated, corpus-verified colloquial nicknames whose official title does NOT
# contain the nickname, each mapping to exactly one base act.  Seeded from the
# highest-frequency unresolved by-name surfaces surfaced by
# ``tools.resolution_miss_analysis`` (sample 3000, seed 1).
STATUTE_NAME_ALIASES: tuple[StatuteNameAlias, ...] = (
    StatuteNameAlias(
        "julkisuuslaki", "1999/621",
        "Laki viranomaisten toiminnan julkisuudesta",
    ),
    StatuteNameAlias(
        "tekijänoikeuslaki", "1961/404",
        "Laki tekijänoikeudesta kirjallisiin ja taiteellisiin teoksiin",
    ),
    StatuteNameAlias(
        "perustuslaki", "1999/731",
        "Suomen perustuslaki",
    ),
    StatuteNameAlias(
        "lahjaverolaki", "1940/378",
        "Perintö- ja lahjaverolaki",
    ),
    StatuteNameAlias(
        "lunastuslaki", "1977/603",
        "Laki kiinteän omaisuuden ja erityisten oikeuksien lunastuksesta",
    ),
    StatuteNameAlias(
        "kemikaaliturvallisuuslaki", "2005/390",
        "Laki vaarallisten kemikaalien ja räjähteiden käsittelyn turvallisuudesta",
    ),
    StatuteNameAlias(
        "itsehallintolaki", "1991/1144",
        "Ahvenanmaan itsehallintolaki",
    ),
    StatuteNameAlias(
        "virkamieslaki", "1994/750",
        "Valtion virkamieslaki",
    ),
    StatuteNameAlias(
        "rahanpesulaki", "2017/444",
        "Laki rahanpesun ja terrorismin rahoittamisen estämisestä",
    ),
    StatuteNameAlias(
        "sananvapauslaki", "2003/460",
        "Laki sananvapauden käyttämisestä joukkoviestinnässä",
    ),
    StatuteNameAlias(
        "toimeentulotukilaki", "1997/1412",
        "Laki toimeentulotuesta",
    ),
    StatuteNameAlias(
        "asiakasmaksulaki", "1992/734",
        "Laki sosiaali- ja terveydenhuollon asiakasmaksuista",
    ),
    StatuteNameAlias(
        "sakkolaki", "2002/672",
        "Laki sakon täytäntöönpanosta",
    ),
    # ------------------------------------------------------------------
    # Second curation pass (bucket (d) "in-data-but-unindexed" — the base act
    # IS in the farchive but is NOT enumerated in ``list_statute_ids()`` (it is
    # oracle/consolidated-only), so the registry artifact never indexed a
    # head-bearing entry for it and the nickname misses.  Each id was VERIFIED by
    # (1) the amendment-parent linkage (``data/finland/amendment_parents.csv``):
    # EVERY amendment title referencing the nickname (``Laki <nick-in-genitive>
    # muuttamisesta`` …) resolves to a SINGLE base parent id; and (2) that id's
    # ``docTitle`` read from the farchive oracle, confirmed to be the SOLE corpus
    # act carrying that nominative title (no same-titled twin).  Two-parent /
    # multi-version nicknames (``valtionosuuslaki``, ``asuntotuotantolaki``,
    # ``tielaki``, ``jakolaki``, ``suojelulaki``, ``tukilaki``, ``voimaanpanolaki``,
    # ``eläkelaki``, ``yhtiölaki`` …) were EXCLUDED to the ambiguous/backlog lane.
    # For these, the nickname EQUALS the act's official title — it misses only
    # because the base act is unindexed, so the nickname is unambiguous by
    # construction.
    StatuteNameAlias(
        "terveydenhoitolaki", "1965/469",
        "Terveydenhoitolaki",
    ),
    StatuteNameAlias(
        "merityöaikalaki", "1976/296",
        "Merityöaikalaki",
    ),
    StatuteNameAlias(
        "väestökirjalaki", "1969/141",
        "Väestökirjalaki",
    ),
    StatuteNameAlias(
        "kunnallislaki", "1976/953",
        "Kunnallislaki",
    ),
    StatuteNameAlias(
        "maatilalaki", "1977/188",
        "Maatilalaki",
    ),
    StatuteNameAlias(
        "lihantarkastuslaki", "1960/160",
        "Lihantarkastuslaki",
    ),
    StatuteNameAlias(
        "maidontarkastuslaki", "1946/558",
        "Maidontarkastuslaki",
    ),
    # True colloquial nickname whose official title DIFFERS from the nickname
    # (verified single-parent, act in corpus, no same-titled twin):
    #  * ``ydinvastuulaki`` — the act titled "Atomivastuulaki" (1972/484), later
    #    colloquially the ydinvastuulaki; every ``ydinvastuulain`` amendment title
    #    resolves to 1972/484 and no act is titled "Ydinvastuulaki".
    #
    # DELIBERATELY EXCLUDED (would denote >1 act over time — the fail-loud/backlog
    # lane): ``maksuperustelaki`` — BOTH 1973/980 (repealed 1992-03-01) and the
    # current 1992/150 are titled "Valtion maksuperustelaki", so a pre-1992
    # citation means 1973/980; a single-id alias would silently mis-pick.
    StatuteNameAlias(
        "ydinvastuulaki", "1972/484",
        "Atomivastuulaki",
    ),
)


__all__ = [
    "STATUTE_NAME_ALIASES",
    "StatuteNameAlias",
]
