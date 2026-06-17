"""Tests for the treaty (SopS) article-reference recognizer.

Covers the residual ARTIKLA recall tail: article references whose governing
instrument is a treaty (explicit ``SopS NNN/YY`` or a ``sopimus``-word cue),
and the disjointness with the EU-by-nickname directive lane.
"""
from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.treaty_article import (
    _SOPIMUS_HEAD_FORMS,
    recognize_treaty_article_refs,
)


# ---------------------------------------------------------------------------
# Morphology-driven treaty-head detection (the retired suffix enumeration)
# ---------------------------------------------------------------------------


def test_sopimus_head_forms_are_morphology_generated_gradated() -> None:
    # The treaty head forms are the M1-generated gradated surfaces of the
    # curated case set, not a hand suffix table; the gradated stem (sopimukse-)
    # is generated, never an ``sopimu`` substring.
    forms = set(_SOPIMUS_HEAD_FORMS)
    assert {"sopimuksen", "sopimukseen", "sopimuksessa", "sopimuksesta"} <= forms
    assert "sopimu" not in forms


def test_sopimus_curated_case_set_excludes_adverbial_cases() -> None:
    # The case set is deliberately curated: the adessive/allative/ablative
    # ("by/onto/from this agreement") do NOT govern an article cite and are
    # excluded, so they cannot introduce false-positive treaty references.
    forms = set(_SOPIMUS_HEAD_FORMS)
    assert "sopimuksella" not in forms  # adessive
    assert "sopimukselle" not in forms  # allative
    assert "sopimukselta" not in forms  # ablative


def test_adessive_sopimus_does_not_emit_treaty_reference() -> None:
    # A near-article adessive ``sopimus`` form (an adverbial "by this agreement")
    # must NOT be read as a governing treaty — that would double-count against
    # the real (often EU-asetus) governor of the article number.
    text = "tällä sopimuksella pantu täytäntöön asetuksen 3 artiklassa"
    assert recognize_treaty_article_refs(text) == []


def test_treaty_head_detected_in_inessive() -> None:
    # The inessive head form is morphology-detected and governs the article.
    text = "tässä yleissopimuksessa 7 artiklassa tarkoitettu"
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].cite_confidence is CiteConfidence.STATUTE_ONLY
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi-treaty-name:sopimus"


def test_explicit_sops_governor_two_articles_exact() -> None:
    """``sopimuksen (SopS 20/66) 2 ja 3 artiklassa`` → 2 EXACT treaty mentions."""
    text = "sopimuksen (SopS 20/66) 2 ja 3 artiklassa tarkoitetut"
    mentions = recognize_treaty_article_refs(text)

    assert len(mentions) == 2
    for m in mentions:
        assert m.cite_kind is CiteKind.TREATY
        assert m.cite_confidence is CiteConfidence.EXACT
        assert m.target_provision_ref is not None
        # 20/66 → year 1966 (2-digit year, century pivot).
        assert m.target_provision_ref.statute_id == "fi:treaty:sops/1966/20"
        assert m.source_span is None

    labels = []
    for m in mentions:
        assert m.target_provision_ref is not None
        labels.append(m.target_provision_ref.section_label)
    assert labels == ["2", "3"]


def test_two_digit_year_pivot_1966() -> None:
    """The 2-digit SopS year 66 expands to 1966, not 2066."""
    text = "sopimuksen (SopS 20/66) 5 artiklassa"
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi:treaty:sops/1966/20"


def test_four_digit_year_preserved() -> None:
    """A 4-digit SopS year is preserved verbatim."""
    text = "yleissopimuksen (SopS 19/2020) 7 artiklassa"
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi:treaty:sops/2020/19"


def test_low_two_digit_year_goes_to_2000s() -> None:
    """A low 2-digit year (below pivot) maps to the 2000s."""
    text = "sopimuksen (SopS 3/05) 1 artiklassa"
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi:treaty:sops/2005/3"


def test_word_cue_only_statute_only() -> None:
    """``1 §:ssä mainitun sopimuksen 13 artiklassa`` → 1 STATUTE_ONLY mention."""
    text = "1 §:ssä mainitun sopimuksen 13 artiklassa tarkoitettu"
    mentions = recognize_treaty_article_refs(text)

    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.TREATY
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    # No SopS number is fabricated: a treaty-name placeholder carries the article.
    assert m.target_provision_ref.statute_id == "fi-treaty-name:sopimus"
    assert m.target_provision_ref.section_label == "13"
    assert m.source_span is None


def test_yleissopimus_word_cue_statute_only() -> None:
    """A ``yleissopimuksen`` word cue (no SopS) is also STATUTE_ONLY."""
    text = "yleissopimuksen 4 artiklassa"
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].cite_confidence is CiteConfidence.STATUTE_ONLY
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi-treaty-name:sopimus"


def test_bare_article_no_governor_emits_nothing() -> None:
    """A bare ``5 artiklan`` with no treaty/EU governor → emit nothing."""
    text = "edellä 5 artiklan nojalla"
    assert recognize_treaty_article_refs(text) == []


def test_eu_nickname_governed_article_not_ours() -> None:
    """An EU-nickname-governed artikla is owned by eu_directive, not this lane."""
    text = "teollisuuspäästödirektiivin 5 artiklassa säädetään"
    # This lane emits nothing (no treaty governor)...
    assert recognize_treaty_article_refs(text) == []
    # ...while the EU directive lane DOES recognise it (disjointness sanity check).
    eu = recognize_eu_directive_refs(text)
    assert len(eu) >= 1
    assert all(d.mention.cite_kind is CiteKind.EU for d in eu)


def test_treaty_governed_article_not_eu() -> None:
    """A treaty-governed artikla is NOT picked up by the EU nickname lane."""
    text = "sopimuksen (SopS 20/66) 2 artiklassa"
    # The EU lane sees no directive/asetus nickname governor → nothing.
    assert recognize_eu_directive_refs(text) == []
    # This lane owns it.
    mentions = recognize_treaty_article_refs(text)
    assert len(mentions) == 1
    assert mentions[0].cite_kind is CiteKind.TREATY


def test_no_artikla_returns_empty() -> None:
    """Guard: text without ``artikla`` short-circuits to empty."""
    assert recognize_treaty_article_refs("sopimuksen (SopS 20/66) 2 §:ssä") == []


def test_surface_text_is_artikla_window() -> None:
    """surface_text is the artikla surface (for document re-anchoring)."""
    text = "sopimuksen (SopS 20/66) 2 ja 3 artiklassa"
    mentions = recognize_treaty_article_refs(text)
    assert mentions
    for m in mentions:
        assert "artikla" in m.surface_text


def test_treaty_article_does_not_leak_preceding_number() -> None:
    """The shared ``_ARTIKLA_RE`` leak fix propagates to the treaty lane: a
    preceding standalone number (here the SopS year ``2020``) is not absorbed
    into the article list."""
    text = "sopimuksen (SopS 19/2020) 8 artiklassa tarkoitettu"
    mentions = recognize_treaty_article_refs(text)
    labels = [
        m.target_provision_ref.section_label
        for m in mentions
        if m.target_provision_ref
    ]
    assert labels == ["8"]
