"""Gate for statute-local ad-hoc EU-nickname → CELEX binding + recovery.

A modern EU-implementing Finnish act coins its OWN nickname for an EU instrument
with ``(jäljempänä <nickname>)`` right after the full cite, then refers to it by
``<nickname> N artikla``. Those later article uses were dropped because the
ad-hoc nickname is not in the static seed. This gate covers:

  - the per-statute binding pre-pass (``build_statute_local_nicknames``) resolving
    a ``jäljempänä``-bound nickname to its CELEX;
  - the EU-directive recognizer consulting the local table so a later
    ``<nickname> N artikla`` (incl. coordinations / inflected uses) resolves to
    the right CELEX article;
  - the FAIL-LOUD discipline (use-before-binding stays declined; the enacting act
    — not a repealed provenance act — is bound);
  - the static ``_SEED`` path is unaffected.
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.eu_nickname_binding import (
    build_statute_local_nicknames,
)


# ---------------------------------------------------------------------------
# Binding pre-pass
# ---------------------------------------------------------------------------


def test_jaljempana_binds_nickname_to_celex() -> None:
    text = (
        "Euroopan parlamentin ja neuvoston asetusta (EY) N:o 999/2001 "
        "(jäljempänä TSE-asetus) sovelletaan."
    )
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma == {"tse-asetus": "32001R0999"}
    # The bound nickname resolves inflected on its head, too.
    assert table.lookup("TSE-asetuksen") == "32001R0999"
    assert table.lookup("tse-asetus") == "32001R0999"


def test_jaljempana_binds_directive_via_head_type_letter() -> None:
    # A directive-headed nickname → L type letter from the head (the cite is the
    # form-less ``(EU) YEAR/NUMBER`` shape; the head supplies the L/R/D type).
    text = (
        "neuvoston direktiivin (EU) 2009/138 soveltamisesta "
        "(jäljempänä vakuutusdirektiivi) tarkoituksena on."
    )
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma == {"vakuutusdirektiivi": "32009L0138"}


def test_binding_picks_enacting_act_not_repealed_provenance() -> None:
    # The nickname binds to the ENACTING act, not the act named only as the
    # object of a repeal in the same long-form title (fail-loud: never the
    # repealed provenance act).
    text = (
        "Euroopan parlamentin ja neuvoston asetuksen (EU) 2021/2116, annettu 2 "
        "päivänä joulukuuta 2021, yhteisen maatalouspolitiikan rahoituksesta sekä "
        "asetuksen (EU) N:o 1306/2013 kumoamisesta (jäljempänä horisontaaliasetus)."
    )
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma["horisontaaliasetus"] == "32021R2116"


def test_non_eu_alias_not_registered() -> None:
    # A domestic ``…laki`` alias is NOT an EU nickname — never registered here.
    text = "ympäristönsuojelulaissa (527/2014, jäljempänä ympäristönsuojelulaki)."
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma == {}


def test_unbindable_nickname_not_registered() -> None:
    # A ``jäljempänä``-bound EU-shaped nickname with NO resolvable cite in window
    # is not registered (stays open), not guessed.
    text = "Tätä sovelletaan, jäljempänä uusi asetus, kaikkiin tilanteisiin."
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma == {}


# ---------------------------------------------------------------------------
# Recovery in the EU-directive recognizer
# ---------------------------------------------------------------------------


def test_local_alias_resolves_later_article_reference() -> None:
    text = (
        "asetusta (EU) 2024/1252 (jäljempänä kriittisten raaka-aineiden asetus) "
        "sovelletaan. Lisäksi kriittisten raaka-aineiden asetuksen 9 artiklan "
        "mukaan toimitaan."
    )
    table = build_statute_local_nicknames(text)
    refs = recognize_eu_directive_refs(text, local_aliases=table)
    assert len(refs) == 1
    assert refs[0].status is CiteConfidence.EXACT
    assert refs[0].celex_candidates == ("32024R1252",)
    assert refs[0].article == "9"
    assert refs[0].mention.cite_kind is CiteKind.EU


def test_long_coined_alias_binds_bounded_no_blowup() -> None:
    # Regression: a long (6-word) coined alias whose words are already-inflected
    # fragments must bind WITHOUT materializing the per-word Cartesian product
    # (which here is ~2e8 strings → OOM). The fix inflects only the head
    # (``asetus``) and holds the modifier fragment invariant, so the table stays
    # small and a later head-inflected use of the SAME alias still resolves.
    text = (
        "komission delegoitua asetusta (EU) 2017/1569 (jäljempänä "
        "tutkimuslääkkeiden hyviä tuotantotapoja koskeva delegoitu asetus) "
        "sovelletaan. Lisäksi tutkimuslääkkeiden hyviä tuotantotapoja koskeva "
        "delegoitu asetuksen 3 artiklan mukaan toimitaan."
    )
    table = build_statute_local_nicknames(text)
    key = "tutkimuslääkkeiden hyviä tuotantotapoja koskeva delegoitu asetus"
    assert table.celex_by_lemma == {key: "32017R1569"}
    # The bound alias resolves on its head-inflected (genitive) surface.
    assert (
        table.lookup(
            "tutkimuslääkkeiden hyviä tuotantotapoja koskeva delegoitu asetuksen"
        )
        == "32017R1569"
    )
    # The surface index is bounded (O(cases)), not the explosive product.
    assert len(table._surface_to_lemma) <= 16


def test_local_alias_article_coordination_enumerates_each_member() -> None:
    text = (
        "asetuksen (EY) N:o 999/2001 (jäljempänä TSE-asetus) nojalla. "
        "Sovelletaan TSE-asetuksen 12 ja 13 artiklan säännöksiä."
    )
    table = build_statute_local_nicknames(text)
    refs = recognize_eu_directive_refs(text, local_aliases=table)
    articles = sorted(r.article for r in refs)
    assert articles == ["12", "13"]
    assert all(r.celex_candidates == ("32001R0999",) for r in refs)
    assert all(r.status is CiteConfidence.EXACT for r in refs)


def test_use_before_binding_stays_unresolved() -> None:
    # A nickname USE before its binding site must NOT resolve to the (later)
    # CELEX — there is no binding in this fragment, so the statute-local table is
    # empty. But ``TSE-asetuksen 12 artiklan`` is a NAMED EU instrument (compound
    # EU-head governing an article), so it is correctly TYPED as an EU reference,
    # STATUTE_ONLY/unresolved (``eu-nickname:`` — no CELEX invented), NOT dropped
    # and NOT mis-typed as a Finnish statute.
    fragment = "Sovelletaan TSE-asetuksen 12 artiklan säännöksiä."
    # No binding in this fragment → empty table → no premature CELEX.
    table = build_statute_local_nicknames(fragment)
    assert table.celex_by_lemma == {}
    refs = recognize_eu_directive_refs(fragment, local_aliases=table)
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.STATUTE_ONLY
    assert r.mention.cite_kind is CiteKind.EU
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "eu-nickname:TSE-asetuksen"
    assert not r.mention.target_provision_ref.statute_id.startswith("celex:")


def test_without_table_local_nickname_is_dropped() -> None:
    # Regression anchor: without the local table the ad-hoc nickname use is
    # dropped (the bug this fix closes).
    text = (
        "asetusta (EU) 2024/1252 (jäljempänä kriittisten raaka-aineiden asetus) "
        "sovelletaan. Lisäksi kriittisten raaka-aineiden asetuksen 9 artiklan "
        "mukaan toimitaan."
    )
    refs = recognize_eu_directive_refs(text, local_aliases=None)
    assert refs == []


# ---------------------------------------------------------------------------
# Static seed unaffected (no regression)
# ---------------------------------------------------------------------------


def test_seed_nickname_still_resolves_with_local_table_present() -> None:
    # An established seed nickname (tietosuoja-asetus → GDPR) resolves via the
    # static seed even when a non-empty local table is also threaded in; the seed
    # always wins (a coined alias never shadows a term-of-art).
    text = (
        "asetuksen (EY) N:o 999/2001 (jäljempänä TSE-asetus) ohella. "
        "Tietosuoja-asetuksen 6 artiklan mukaan käsitellään tietoja."
    )
    table = build_statute_local_nicknames(text)
    assert table.celex_by_lemma  # non-empty (TSE-asetus bound)
    refs = recognize_eu_directive_refs(text, local_aliases=table)
    gdpr = [r for r in refs if r.celex_candidates == ("32016R0679",)]
    assert len(gdpr) == 1
    assert gdpr[0].article == "6"
    assert gdpr[0].status is CiteConfidence.EXACT


def test_seed_nickname_resolves_without_local_table() -> None:
    refs = recognize_eu_directive_refs(
        "tietosuoja-asetuksen 6 artiklan mukaan.", local_aliases=None
    )
    assert len(refs) == 1
    assert refs[0].celex_candidates == ("32016R0679",)
