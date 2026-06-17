"""Gate for EU directive-by-nickname + article reference recognition.

Covers the ``eu.directive_article`` family (FI Reference Catalogue §2/§3),
previously 0% captured:
  - nickname -> single CELEX  => EXACT, one mention per coordinated article
  - unknown nickname          => STATUTE_ONLY
  - ambiguous-seeded nickname => AMBIGUOUS (all candidates, no silent pick)
  - single-article phrase     => exactly one article mention
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.registries import eu_nickname


# ---------------------------------------------------------------------------
# Registry-level
# ---------------------------------------------------------------------------


def test_registry_single_inflected() -> None:
    res = eu_nickname.lookup("teollisuuspäästödirektiivin")
    assert res.status is eu_nickname.RegistryStatus.SINGLE
    assert res.candidates == ("32010L0075",)


def test_registry_multiword_inflected() -> None:
    # Both the adjective (yleinen->yleisen) and the head (asetus->asetuksen)
    # inflect; the morphology-backed index must still resolve.
    res = eu_nickname.lookup("yleisen tietosuoja-asetuksen")
    assert res.status is eu_nickname.RegistryStatus.SINGLE
    assert res.candidates == ("32016R0679",)


def test_registry_unknown_is_none() -> None:
    res = eu_nickname.lookup("foobardirektiivin")
    assert res.status is eu_nickname.RegistryStatus.NONE
    assert res.candidates == ()


def test_registry_ambiguous_multiple() -> None:
    res = eu_nickname.lookup("jätedirektiivin")
    assert res.status is eu_nickname.RegistryStatus.MULTIPLE
    assert set(res.candidates) == {"32008L0098", "32006L0012"}


# ---------------------------------------------------------------------------
# Recognizer-level
# ---------------------------------------------------------------------------


def test_nickname_article_coordination_exact() -> None:
    refs = recognize_eu_directive_refs("teollisuuspäästödirektiivin 33 ja 35 artiklassa")
    assert len(refs) == 2
    articles = [r.article for r in refs]
    assert articles == ["33", "35"]
    for r in refs:
        assert r.status is CiteConfidence.EXACT
        assert r.celex_candidates == ("32010L0075",)
        assert r.mention.cite_kind is CiteKind.EU
        assert r.mention.target_provision_ref is not None
        assert r.mention.target_provision_ref.section_label == r.article
        assert r.mention.target_provision_ref.statute_id == "celex:32010L0075"


def test_unknown_nickname_is_statute_only() -> None:
    refs = recognize_eu_directive_refs("foobardirektiivin 4 artiklassa")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.STATUTE_ONLY
    assert r.celex_candidates == ()
    assert r.article == "4"
    # Article path is NOT dropped — the instrument identity is carried textually.
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.section_label == "4"
    assert "foobardirektiivin" in r.mention.target_provision_ref.statute_id


def test_ambiguous_nickname_emits_all_candidates() -> None:
    refs = recognize_eu_directive_refs("jätedirektiivin 7 artiklassa")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.AMBIGUOUS
    assert set(r.celex_candidates) == {"32008L0098", "32006L0012"}
    # Fail-loud: no silent pick — the resolved statute_id is not a single CELEX.
    assert r.mention.target_provision_ref is not None
    assert not r.mention.target_provision_ref.statute_id.startswith("celex:")


def test_single_article() -> None:
    refs = recognize_eu_directive_refs("teollisuuspäästödirektiivin 12 artiklan")
    assert len(refs) == 1
    assert refs[0].article == "12"
    assert refs[0].status is CiteConfidence.EXACT


def test_article_range_expands() -> None:
    refs = recognize_eu_directive_refs("sivutuoteasetuksen 12—14 artiklassa")
    assert [r.article for r in refs] == ["12", "13", "14"]
    assert all(r.status is CiteConfidence.EXACT for r in refs)


def test_bare_article_without_nickname_skipped() -> None:
    # No governing EU nickname in the lookbehind: this is a plain same-instrument
    # article reference owned by other lanes, not an EU-by-nickname reference.
    refs = recognize_eu_directive_refs("12 artiklan mukaisesti")
    assert refs == []
