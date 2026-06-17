"""Gate for compound-nickname derivation in the statute-name registry.

A pervasive Finnish phenomenon: an act *titled* ``Laki X:stä`` (laki + a noun in
the elative) is *cited* by the compound nickname ``Xlaki`` (noun-nominative +
head).  ``derive_nicknames`` recovers that nickname for the cleanly-reversible
single-noun-elative subset and indexes it under the same id, so a citation like
``verotusmenettelylain`` resolves to ``Laki verotusmenettelystä``.

The reverse-morphology step is bounded and verified: it must NOT guess an
irregular/gradating reverse-inflection (a wrong nickname is worse than a miss).
"""

from __future__ import annotations

import datetime as dt

from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    build_registry,
    derive_nicknames,
)


# ---------------------------------------------------------------------------
# derive_nicknames — the bounded, verified reverse-morphology helper.
# ---------------------------------------------------------------------------


def test_clean_single_noun_elative_yields_nickname() -> None:
    """``Laki verotusmenettelystä`` -> the compound nickname ``verotusmenettelylaki``."""
    assert derive_nicknames("Laki verotusmenettelystä") == ["verotusmenettelylaki"]


def test_nickname_head_matches_title_head() -> None:
    """An ``Asetus X:stä`` title nicknames to ``Xasetus``, not ``Xlaki``."""
    assert derive_nicknames("Asetus meripelastuspalvelusta") == [
        "meripelastuspalveluasetus"
    ]
    assert derive_nicknames("Laki meripelastuspalvelusta") == [
        "meripelastuspalvelulaki"
    ]


def test_trailing_period_tolerated() -> None:
    """A title with a trailing period (older Finlex titles) still nicknames."""
    assert derive_nicknames("Laki nostorahasta.") == ["nostorahalaki"]


def test_multiword_title_not_nicknamed() -> None:
    """A multi-word elative phrase is not a single-noun nickname — skipped."""
    assert derive_nicknames("Laki yleisistä kokouksista") == []
    assert derive_nicknames("Laki Korkeimmasta oikeudesta") == []


def test_amendment_title_not_nicknamed() -> None:
    """An amendment title ("... annetun lain muuttamisesta") is never nicknamed."""
    assert (
        derive_nicknames("Laki verotusmenettelystä annetun lain muuttamisesta") == []
    )


def test_kpt_gradation_is_not_guessed() -> None:
    """``Laki kuntajaosta`` must NOT yield a guessed ``kuntajaolaki``.

    The nominative is ``kuntajako`` (k-deletion gradation: ``jako`` -> ``jaosta``);
    the reverse step is ambiguous, so the nickname is skipped — never the wrong
    ``kuntajao``-based guess.
    """
    assert derive_nicknames("Laki kuntajaosta") == []


def test_nasal_gradation_is_not_guessed() -> None:
    """``Laki ... lautakunnasta`` must NOT yield a ``...lautakunna``-based guess.

    The nominative is ``...lautakunta`` (``nt`` -> weak ``nn`` in the oblique
    stem); the ``t`` is unrecoverable, so the nickname is skipped.
    """
    assert derive_nicknames("Laki tarkastuslautakunnasta") == []
    assert derive_nicknames("Asetus hallinnosta") == []


def test_head_already_present_title_not_handled_here() -> None:
    """A title already ending in a known head is inflected, not nicknamed."""
    assert derive_nicknames("Holhouslaki") == []
    assert derive_nicknames("Ajoneuvoverolaki") == []


# ---------------------------------------------------------------------------
# End-to-end through the registry: the nickname surface resolves.
# ---------------------------------------------------------------------------


def test_nickname_citation_resolves_through_registry() -> None:
    """``lookup("verotusmenettelylain")`` resolves to the elative-titled act."""
    reg = build_registry(
        [
            StatuteNameEntry(
                statute_id="1995/1558",
                canonical_title="Laki verotusmenettelystä",
                valid_from=dt.date(1995, 12, 18),
                valid_to=None,
            ),
        ]
    )
    # The official-title surface still resolves.
    assert reg.lookup("Laki verotusmenettelystä").status == "single"
    # The compound nickname (cited form, genitive) now ALSO resolves.
    nom = reg.lookup("verotusmenettelylaki")
    assert nom.status == "single"
    assert nom.candidates[0].statute_id == "1995/1558"
    gen = reg.lookup("verotusmenettelylain")
    assert gen.status == "single"
    assert gen.candidates[0].statute_id == "1995/1558"


def test_irregular_title_introduces_no_resolution() -> None:
    """A gradating title indexes NO nickname, so its compound never resolves.

    Fail-loud: a citation that would have wanted ``kuntajakolaki`` correctly
    misses (``none``) rather than resolving to a wrong-stem guess.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                statute_id="9999/1",
                canonical_title="Laki kuntajaosta",
                valid_from=dt.date(2000, 1, 1),
                valid_to=None,
            ),
        ]
    )
    # The official title resolves; no nickname was synthesized.
    assert reg.lookup("Laki kuntajaosta").status == "single"
    assert reg.lookup("kuntajaolaki").status == "none"
    assert reg.lookup("kuntajakolaki").status == "none"


def test_temporal_nickname_collision_is_ambiguous_not_silent() -> None:
    """Two acts deriving the same nickname over time land ``multiple`` (safe).

    An act re-enacted under a renamed compound head ("Laki ajoneuvoverosta" ->
    later "Ajoneuvoverolaki") may share the nickname; the registry must list both
    and never silently pick — the fail-loud guarantee.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                statute_id="1996/1111",
                canonical_title="Laki ajoneuvoverosta",
                valid_from=dt.date(1996, 12, 1),
                valid_to=dt.date(2004, 1, 1),
            ),
            StatuteNameEntry(
                statute_id="2003/1281",
                canonical_title="Ajoneuvoverolaki",
                valid_from=dt.date(2004, 1, 1),
                valid_to=None,
            ),
        ]
    )
    res = reg.lookup("ajoneuvoverolaki")
    assert res.status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1996/1111", "2003/1281"}
    # The temporal filter still disambiguates a dated citation.
    dated = reg.lookup("ajoneuvoverolaki", as_of=dt.date(2010, 1, 1))
    assert dated.status == "single"
    assert dated.candidates[0].statute_id == "2003/1281"
