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
