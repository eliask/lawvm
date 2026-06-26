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

from lawvm.finland.references.registries import eu_nickname
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
    assert reg.lookup("Laki verotusmenettelystä").registry_status == "single"
    # The compound nickname (cited form, genitive) now ALSO resolves.
    nom = reg.lookup("verotusmenettelylaki")
    assert nom.registry_status == "single"
    assert nom.candidates[0].statute_id == "1995/1558"
    gen = reg.lookup("verotusmenettelylain")
    assert gen.registry_status == "single"
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
    assert reg.lookup("Laki kuntajaosta").registry_status == "single"
    assert reg.lookup("kuntajaolaki").registry_status == "none"
    assert reg.lookup("kuntajakolaki").registry_status == "none"


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
    assert res.registry_status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1996/1111", "2003/1281"}
    # The temporal filter still disambiguates a dated citation.
    dated = reg.lookup("ajoneuvoverolaki", as_of=dt.date(2010, 1, 1))
    assert dated.registry_status == "single"
    assert dated.candidates[0].statute_id == "2003/1281"


# ---------------------------------------------------------------------------
# EU-instrument nickname -> CELEX registry (eu_nickname seed coverage).
#
# Each entry's CELEX is verified against the EU act it actually names (EUR-Lex /
# CELLAR). Fail-loud: a nickname that genuinely maps to >1 EU act over time (or
# across sectors) is seeded MULTIPLE — the registry never silently picks one.
# ---------------------------------------------------------------------------


def test_eu_single_regulation_resolves_inflected() -> None:
    """Newly-seeded unambiguous regulations resolve from any inflected head.

    vakavaraisuusasetus = CRR (EU) 575/2013; biosidiasetus = Biocidal Products
    (EU) 528/2012; kasvinsuojeluaineasetus = PPP (EC) 1107/2009;
    terveysväiteasetus = Reg (EC) 1924/2006; elintarviketietoasetus = FIC (EU)
    1169/2011. The ``asetus`` head is a known morphology head, so the genitive
    surface (``...asetuksen``) resolves to the same single CELEX.
    """
    cases = {
        "vakavaraisuusasetuksen": "32013R0575",
        "biosidiasetuksen": "32012R0528",
        "kasvinsuojeluaineasetuksen": "32009R1107",
        "terveysväiteasetuksen": "32006R1924",
        "elintarviketietoasetuksen": "32011R1169",
    }
    for surface, celex in cases.items():
        res = eu_nickname.lookup(surface)
        assert res.registry_status is eu_nickname.RegistryStatus.SINGLE, surface
        assert res.candidates == (celex,), surface


def test_eu_temporally_ambiguous_directives_are_multiple() -> None:
    """Successor-reuses-predecessor nicknames are MULTIPLE, never a silent pick.

    maksupalveludirektiivi (PSD1 2007/64 / PSD2 (EU) 2015/2366),
    rahoitusvälinedirektiivi (MiFID I 2004/39 / MiFID II 2014/65),
    energiatehokkuusdirektiivi (2012/27 / recast (EU) 2023/1791): each genuinely
    floats between two acts over time, so the registry lists both and refuses.
    """
    cases = {
        "maksupalveludirektiivin": {"32007L0064", "32015L2366"},
        "rahoitusvälinedirektiivin": {"32004L0039", "32014L0065"},
        "energiatehokkuusdirektiivin": {"32012L0027", "32023L1791"},
    }
    for surface, celexes in cases.items():
        res = eu_nickname.lookup(surface)
        assert res.registry_status is eu_nickname.RegistryStatus.MULTIPLE, surface
        assert set(res.candidates) == celexes, surface


def test_eu_tietosuojadirektiivi_is_not_gdpr() -> None:
    """``tietosuojadirektiivi`` (the *directive*) is ambiguous and NOT the GDPR.

    GDPR (32016R0679) is consistently an *asetus* in Finnish prose; the bare
    *direktiivi* word splits between the old Data Protection Directive 95/46/EY
    and the Law Enforcement Directive (EU) 2016/680. Fail-loud: the GDPR CELEX
    must not appear among the candidates.
    """
    res = eu_nickname.lookup("tietosuojadirektiivin")
    assert res.registry_status is eu_nickname.RegistryStatus.MULTIPLE
    assert set(res.candidates) == {"31995L0046", "32016L0680"}
    assert "32016R0679" not in res.candidates
    # The GDPR *asetus* forms remain single and distinct.
    assert eu_nickname.lookup("yleisen tietosuoja-asetuksen").candidates == (
        "32016R0679",
    )


def test_eu_cross_domain_directive_is_multiple() -> None:
    """``vakavaraisuusdirektiivi`` splits cross-sector (banking vs insurance).

    CRD IV 2013/36/EU vs Solvency II 2009/138/EY — resolvable only by sector
    context the registry does not see, so it lists both. Contrast with the
    unambiguous *asetus* form (CRR), which stays single.
    """
    res = eu_nickname.lookup("vakavaraisuusdirektiivin")
    assert res.registry_status is eu_nickname.RegistryStatus.MULTIPLE
    assert set(res.candidates) == {"32013L0036", "32009L0138"}
    assert eu_nickname.lookup("vakavaraisuusasetuksen").registry_status is (
        eu_nickname.RegistryStatus.SINGLE
    )


# ---------------------------------------------------------------------------
# Inflected-surface expansion: bounded + case-agreeing (no Cartesian blowup).
# ---------------------------------------------------------------------------


def test_multiword_seed_nickname_is_case_synchronized_diagonal() -> None:
    """``yleinen tietosuoja-asetus`` expands on the case-AGREEING diagonal.

    Both words inflect into the SAME case (``yleisen tietosuoja-asetuksen``),
    never the Cartesian product of independent per-word variants (which would
    fabricate incoherent mixed-case combos like ``yleisen tietosuoja-asetus``).
    The bare nominative is always present; the count stays tiny (~one per case).
    """
    surfaces = eu_nickname._inflected_surfaces("yleinen tietosuoja-asetus")
    assert "yleinen tietosuoja-asetus" in surfaces  # bare lemma
    assert "yleisen tietosuoja-asetuksen" in surfaces  # genitive diagonal
    # No fabricated mixed-case surface (head inflected, modifier in nominative).
    assert "yleinen tietosuoja-asetuksen" not in surfaces
    assert len(surfaces) <= 16  # O(cases), not a product


def test_long_document_alias_is_bounded_head_only() -> None:
    """A long document-derived alias inflects ONLY its head; no blowup.

    ``build_statute_local_nicknames`` reuses ``_inflected_surfaces`` on
    arbitrary-length, already-inflected coined aliases. The full Cartesian
    product of per-word variants here is ~2e8 strings (OOM). The fix inflects
    only the head noun (``asetus``) and holds the modifier fragment invariant.
    """
    alias = "tutkimuslääkkeiden hyviä tuotantotapoja koskeva delegoitu asetus"
    surfaces = eu_nickname._inflected_surfaces(alias)
    assert alias in surfaces  # bare lemma
    # Head inflects (genitive); the modifier fragment is held verbatim.
    assert (
        "tutkimuslääkkeiden hyviä tuotantotapoja koskeva delegoitu asetuksen"
        in surfaces
    )
    # Bounded: O(cases), nowhere near the ~2e8-string Cartesian product.
    assert len(surfaces) <= 16


def test_eu_bare_vesidirektiivi_is_ambiguous_not_single() -> None:
    """``vesidirektiivi`` is not a stable term-of-art — seeded MULTIPLE.

    The bare word floats between Drinking Water 98/83/EY, its recast (EU)
    2020/2184, and the Water Framework Directive 2000/60/EY. Fail-loud: it must
    not silently resolve to a single act. The *qualified* compound
    ``vesipuitedirektiivi`` stays unambiguously single.
    """
    res = eu_nickname.lookup("vesidirektiivin")
    assert res.registry_status is eu_nickname.RegistryStatus.MULTIPLE
    assert set(res.candidates) == {"31998L0083", "32020L2184", "32000L0060"}
    qualified = eu_nickname.lookup("vesipuitedirektiivin")
    assert qualified.registry_status is eu_nickname.RegistryStatus.SINGLE
    assert qualified.candidates == ("32000L0060",)
