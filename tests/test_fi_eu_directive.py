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
from lawvm.finland.references.eu_directive import (
    _ARTIKLA_RE,
    _EU_HEAD_FORMS,
    _expand_articles,
    recognize_eu_directive_refs,
)
from lawvm.finland.references.registries import eu_nickname


# ---------------------------------------------------------------------------
# Morphology-driven head detection (the retired suffix-substring matcher)
# ---------------------------------------------------------------------------


def test_eu_head_forms_are_morphology_generated_not_substring() -> None:
    # The head alternation is the M1-generated paradigm of direktiivi + asetus,
    # including the gradated stem forms (asetuksen, not an ``asetu`` substring),
    # so the consonant-gradation substring bug class cannot occur.
    forms = set(_EU_HEAD_FORMS)
    assert "asetuksen" in forms  # gradated genitive (-Us -> -Ukse-)
    assert "direktiivin" in forms
    assert "direktiivi" in forms
    # No bare ``asetu`` / ``direktiiv`` substring stem leaks into the alternation.
    assert "asetu" not in forms
    assert "direktiiv" not in forms


def test_eu_head_plural_external_local_supplement_present() -> None:
    # M1's reference_v1 profile cannot emit the plural external local cases;
    # they are added via the explicit sound supplement so the head is still
    # detected in e.g. ``näillä direktiiveillä``.
    forms = set(_EU_HEAD_FORMS)
    assert "direktiiveillä" in forms  # plural adessive (M1 boundary supplement)
    assert "asetuksilla" in forms


def test_eu_head_detected_in_plural_adessive() -> None:
    # The plural adessive head form (the M1-boundary supplement) is detected as a
    # governing EU-instrument head and resolves via the adjacent formal cite,
    # exactly as the retired ``direktiiv`` substring matcher did:
    # "direktiiveillä 2014/86/EU 2 artiklassa" -> 32014L0086.
    refs = recognize_eu_directive_refs("neuvoston direktiiveillä 2014/86/EU 2 artiklassa")
    assert len(refs) == 1
    assert refs[0].status is CiteConfidence.EXACT
    assert refs[0].celex_candidates == ("32014L0086",)


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


def test_unknown_bare_head_without_formal_cite_not_emitted() -> None:
    # A nickname-shaped head unknown to the registry AND with no adjacent formal
    # EU cite is NOT a resolvable EU-by-nickname reference: emitting a bare
    # ``eu-nickname:<head>`` STATUTE_ONLY would be a pure false positive (the
    # article number is governed elsewhere) and would double-count against the
    # formal-cite lane. Fail-loud: emit nothing.
    refs = recognize_eu_directive_refs("foobardirektiivin 4 artiklassa")
    assert refs == []


def test_unknown_bare_head_with_inline_formal_cite_resolves() -> None:
    # Sub-case (b): a bare head NOT in the registry but followed by an inline
    # formal EU cite resolves to that cite's CELEX. The head supplies the CELEX
    # type letter (direktiivi → L), the cite supplies (year, number):
    # "direktiivin 2009/138/EY 268 artiklan" → 32009L0138.
    refs = recognize_eu_directive_refs("direktiivin 2009/138/EY 268 artiklan")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.EXACT
    assert r.celex_candidates == ("32009L0138",)
    assert r.article == "268"
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "celex:32009L0138"


def test_unknown_bare_head_with_eu_year_cite_resolves_regulation() -> None:
    # Sub-case (b), regulation form: "asetuksen (EU) 2018/1805 30 artiklan"
    # → asetus → R → 32018R1805.
    refs = recognize_eu_directive_refs("asetuksen (EU) 2018/1805 30 artiklan")
    assert len(refs) == 1
    assert refs[0].status is CiteConfidence.EXACT
    assert refs[0].celex_candidates == ("32018R1805",)
    assert refs[0].article == "30"


def test_bare_domestic_asetus_anaphora_not_emitted() -> None:
    # A domestic/anaphoric ``asetus`` whose article number is governed elsewhere
    # (no registry hit, no adjacent formal EU cite) must not be emitted.
    refs = recognize_eu_directive_refs("tässä asetuksessa tarkoitetun 5 artiklan")
    assert refs == []


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


# ---------------------------------------------------------------------------
# Article number-list must not leak across a preceding standalone number
# ---------------------------------------------------------------------------


def test_artikla_number_does_not_absorb_preceding_year() -> None:
    # "2004 8 artiklassa": the bare "2004" is a preceding number, not an article.
    # The list must capture only the contiguous "8".
    m = _ARTIKLA_RE.search("säädöksen 2004 8 artiklassa")
    assert m is not None
    assert _expand_articles(m.group("nums")) == ["8"]


def test_artikla_list_does_not_absorb_preceding_number() -> None:
    # "2012 13 ja 14 artiklan": the real articles are 13 and 14; the leading
    # "2012" must not collapse the list to a single "2012".
    m = _ARTIKLA_RE.search("vuoden 2012 13 ja 14 artiklan")
    assert m is not None
    assert _expand_articles(m.group("nums")) == ["13", "14"]


def test_artikla_plain_list_and_range_preserved() -> None:
    # Connector-joined lists and ranges are unaffected.
    m1 = _ARTIKLA_RE.search("33 ja 35 artiklassa")
    assert m1 is not None and _expand_articles(m1.group("nums")) == ["33", "35"]
    m2 = _ARTIKLA_RE.search("33—35 artiklassa")
    assert m2 is not None and _expand_articles(m2.group("nums")) == ["33", "34", "35"]
    m3 = _ARTIKLA_RE.search("1, 2 ja 3 artiklassa")
    assert m3 is not None and _expand_articles(m3.group("nums")) == ["1", "2", "3"]
