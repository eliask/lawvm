"""Gate for the curated colloquial-nickname alias table (Index B recall lever).

A by-name citation by a colloquial abbreviation whose official title does NOT
contain the nickname (``julkisuuslaki`` for "Laki viranomaisten toiminnan
julkisuudesta") cannot be derived by the generation-first registry — it is
recovered by the curated :data:`STATUTE_NAME_ALIASES` table wired into
``build_registry``.  This gate pins:

  * a seeded alias resolves (``single``) to its mapped id through ``lookup``;
  * the alias coexists with generated surfaces and stays fail-loud
    (``multiple``) when a key would name more than one act;
  * an unverified/excluded surface (a temporally-ambiguous or merely-plausible
    nickname) is NOT in the table — quality discipline, no silent picks.
"""

from __future__ import annotations

import datetime as dt

from lawvm.finland.references.registries.statute_name import (
    STATUTE_NAME_ALIASES,
    StatuteNameAlias,
    build_registry,
)


# ---------------------------------------------------------------------------
# A seeded alias resolves via lookup (single -> mapped id).
# ---------------------------------------------------------------------------


def test_seeded_alias_resolves_single() -> None:
    """``julkisuuslaki`` (not derivable from its title) resolves to 1999/621."""
    reg = build_registry([])  # no generated entries; aliases on by default
    res = reg.lookup("julkisuuslaki")
    assert res.registry_status == "single"
    assert [c.statute_id for c in res.candidates] == ["1999/621"]
    # Provenance: the candidate carries the official title it abbreviates.
    assert res.candidates[0].canonical_title == (
        "Laki viranomaisten toiminnan julkisuudesta"
    )


def test_every_seeded_alias_resolves_single_to_its_mapped_id() -> None:
    """Each curated alias resolves to exactly its mapped id (no cross-talk)."""
    reg = build_registry([])
    for alias in STATUTE_NAME_ALIASES:
        res = reg.lookup(alias.alias_key)
        assert res.registry_status == "single", (alias.alias_key, res.registry_status)
        assert [c.statute_id for c in res.candidates] == [alias.statute_id]


def test_alias_only_hit_reports_via_alias() -> None:
    """An alias-ONLY surface key reports ``via_alias=True`` (drives APPROXIMATE)."""
    reg = build_registry([])  # aliases-only, no generated entries
    res = reg.lookup("julkisuuslaki")
    assert res.registry_status == "single"
    assert res.via_alias is True


def test_generated_surface_is_not_via_alias() -> None:
    """A generation-derived surface stays ``via_alias=False`` (real EXACT match).

    ``via_alias`` marks ONLY alias-provenanced keys; a key produced by generation
    from an official title must not be flagged, so the resolver keeps it EXACT.
    """
    reg = build_registry([("1096/1996", "Luonnonsuojelulaki")])
    res = reg.lookup("luonnonsuojelulaki")
    assert res.registry_status == "single"
    assert res.via_alias is False


def test_alias_key_also_generated_is_not_via_alias() -> None:
    """A key BOTH aliased and generated is a real EXACT surface (not alias-only).

    When a generated title produces the SAME normalized key an alias also names
    (for the SAME id), the hit is a parsed-exact surface: ``via_alias`` must be
    False so the resolver does not downgrade a real EXACT match to APPROXIMATE.
    """
    custom = (StatuteNameAlias("metsälaki", "1093/1996", "Metsälaki"),)
    reg = build_registry([("1093/1996", "Metsälaki")], aliases=custom)
    res = reg.lookup("metsälaki")
    assert res.registry_status == "single"
    assert [c.statute_id for c in res.candidates] == ["1093/1996"]
    assert res.via_alias is False


def test_second_pass_verified_aliases_present_and_single() -> None:
    """The bucket-(d) unindexed-base-act aliases resolve single to their id.

    These acts ARE in the corpus but are oracle/consolidated-only (absent from
    ``list_statute_ids()``), so the generated registry never indexed them; the
    curated alias binds the nickname directly.  Each was verified 1:1 by the
    amendment-parent linkage + a unique corpus title (no same-named twin).
    """
    reg = build_registry([])
    expected = {
        "terveydenhoitolaki": "1965/469",
        "merityöaikalaki": "1976/296",
        "väestökirjalaki": "1969/141",
        "kunnallislaki": "1976/953",
        "maatilalaki": "1977/188",
        "lihantarkastuslaki": "1960/160",
        "maidontarkastuslaki": "1946/558",
        "ydinvastuulaki": "1972/484",
    }
    keys = {a.alias_key: a.statute_id for a in STATUTE_NAME_ALIASES}
    for nick, sid in expected.items():
        assert keys.get(nick) == sid, (nick, keys.get(nick))
        res = reg.lookup(nick)
        assert res.registry_status == "single", (nick, res.registry_status)
        assert [c.statute_id for c in res.candidates] == [sid], nick
        assert res.via_alias is True, nick


def test_temporally_ambiguous_second_pass_surfaces_excluded() -> None:
    """Nicknames denoting >1 act over time stay OUT of the curated table.

    ``maksuperustelaki`` (two acts both titled "Valtion maksuperustelaki",
    1973/980 repealed 1992 and the current 1992/150) and the multi-parent
    ``valtionosuuslaki`` / ``tielaki`` / ``jakolaki`` / ``suojelulaki`` /
    ``tukilaki`` / ``eläkelaki`` were EXCLUDED — a single-id alias would force a
    silent temporal/subject mis-pick.
    """
    keys = {a.alias_key for a in STATUTE_NAME_ALIASES}
    for forbidden in (
        "maksuperustelaki", "valtionosuuslaki", "tielaki", "jakolaki",
        "suojelulaki", "tukilaki", "eläkelaki", "voimaanpanolaki",
        "asuntotuotantolaki", "yhtiölaki",
    ):
        assert forbidden not in keys, forbidden


def test_alias_keys_are_normalized_lowercase_single_token() -> None:
    """Alias keys match the by-name normalization (lower-case, no extra space)."""
    for alias in STATUTE_NAME_ALIASES:
        assert alias.alias_key == " ".join(alias.alias_key.lower().split())


# ---------------------------------------------------------------------------
# Coexistence + fail-loud: an alias never silently overrides a generated id.
# ---------------------------------------------------------------------------


def test_alias_coexists_with_generated_surface() -> None:
    """A generated title and the alias table both populate the same registry."""
    reg = build_registry(
        [("1996/1093", "Metsälaki", dt.date(1996, 1, 1), None)],
    )
    # Generated nominative surface still resolves.
    assert reg.lookup("metsälaki").registry_status == "single"
    assert reg.lookup("metsälaki").candidates[0].statute_id == "1996/1093"
    # Curated alias still resolves alongside it.
    assert reg.lookup("perustuslaki").candidates[0].statute_id == "1999/731"


def test_alias_colliding_with_a_different_generated_id_is_fail_loud() -> None:
    """An alias key shared by a DIFFERENT generated id lands ``multiple``.

    The alias never silently overrides: when a generated title produces the same
    key for another act, ``lookup`` reports both candidates (ambiguous), which
    the caller resolves — the registry never picks.
    """
    custom = (StatuteNameAlias("perustuslaki", "1999/731", "Suomen perustuslaki"),)
    # A different act whose generated nominative surface IS ``perustuslaki``.
    reg = build_registry(
        [("1919/94-001", "Perustuslaki", dt.date(1919, 1, 1), None)],
        aliases=custom,
    )
    res = reg.lookup("perustuslaki")
    assert res.registry_status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1999/731", "1919/94-001"}


def test_aliases_can_be_disabled() -> None:
    """``aliases=None`` builds a generation-only registry (delta measurement)."""
    reg = build_registry([], aliases=None)
    assert reg.lookup("julkisuuslaki").registry_status == "none"


# ---------------------------------------------------------------------------
# Quality discipline: excluded surfaces are NOT in the table.
# ---------------------------------------------------------------------------


def test_unverified_or_ambiguous_surfaces_excluded() -> None:
    """Temporally-ambiguous / non-nickname surfaces must stay out of the table.

    ``kuntalaki`` (two acts over time, both literally titled "Kuntalaki") and
    ``hankintalaki`` (three "Laki julkisista hankinnoista" over time) would force
    a silent temporal pick — they are deliberately left to the registry's own
    ``multiple`` outcome and must never appear as a single-id alias.
    """
    keys = {a.alias_key for a in STATUTE_NAME_ALIASES}
    for forbidden in ("kuntalaki", "hankintalaki", "tilintarkastuslaki",
                      "luottolaitoslaki", "yhteistoimintalaki"):
        assert forbidden not in keys


def test_no_duplicate_alias_keys() -> None:
    """Each alias key is unique (no two curated entries fight over one key)."""
    keys = [a.alias_key for a in STATUTE_NAME_ALIASES]
    assert len(keys) == len(set(keys))


def test_alias_ids_have_statute_id_shape() -> None:
    """Every mapped id is a concrete ``NNN/YYYY``-shaped statute id, never a guess."""
    import re

    pat = re.compile(r"^\d{4}/\d{1,5}(?:-\d+)?$")
    for alias in STATUTE_NAME_ALIASES:
        assert pat.match(alias.statute_id), alias.statute_id
        assert alias.official_title.strip()
