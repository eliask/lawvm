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
    _celex_from_formal_cite,
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
    assert res.registry_status is eu_nickname.RegistryStatus.SINGLE
    assert res.candidates == ("32010L0075",)


def test_registry_multiword_inflected() -> None:
    # Both the adjective (yleinen->yleisen) and the head (asetus->asetuksen)
    # inflect; the morphology-backed index must still resolve.
    res = eu_nickname.lookup("yleisen tietosuoja-asetuksen")
    assert res.registry_status is eu_nickname.RegistryStatus.SINGLE
    assert res.candidates == ("32016R0679",)


def test_registry_unknown_is_none() -> None:
    res = eu_nickname.lookup("foobardirektiivin")
    assert res.registry_status is eu_nickname.RegistryStatus.NONE
    assert res.candidates == ()


def test_registry_ambiguous_multiple() -> None:
    res = eu_nickname.lookup("jätedirektiivin")
    assert res.registry_status is eu_nickname.RegistryStatus.MULTIPLE
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


def test_named_eu_instrument_without_cite_typed_statute_only() -> None:
    # A registry-MISS *named* EU instrument (a compound EU-head nickname directly
    # governing an ``N artikla``) with no adjacent formal cite is TYPED as an EU
    # instrument reference, STATUTE_ONLY/unresolved, routed to ``eu-nickname:`` —
    # NOT mis-typed as a Finnish ``fi-name:`` statute. Finnish acts use § not
    # artikla, so the article-governed compound EU-head is unambiguously EU.
    # Tag-don't-guess: no CELEX is invented.
    refs = recognize_eu_directive_refs("foobardirektiivin 4 artiklassa")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.STATUTE_ONLY
    assert r.mention.cite_kind is CiteKind.EU
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "eu-nickname:foobardirektiivin"
    assert not r.mention.target_provision_ref.statute_id.startswith("celex:")
    assert r.article == "4"


def test_bare_standalone_head_without_modifier_not_emitted() -> None:
    # A BARE standalone EU head with NO glued compound modifier (the whole token
    # IS an inflected ``asetus``/``direktiivi``) carries no instrument identity —
    # it is anaphoric/domestic, its article number governed elsewhere. Emitting a
    # bare ``eu-nickname:<head>`` STATUTE_ONLY would be a pure false positive and
    # double-count the formal-cite lane. Fail-loud: emit nothing.
    refs = recognize_eu_directive_refs("mainitun direktiivin 4 artiklassa")
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


def test_legacy_two_digit_year_directive_resolves() -> None:
    # Legacy pre-2000 directive cite written year-first with a 2-digit year:
    # "direktiivin 96/53/EY 3 artiklan" → year 96 → 1996, directive → L →
    # 31996L0053. Without the 2-digit-year relaxation this yielded no CELEX.
    refs = recognize_eu_directive_refs("direktiivin 96/53/EY 3 artiklan")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.EXACT
    assert r.celex_candidates == ("31996L0053",)
    assert r.article == "3"
    assert r.mention.target_provision_ref is not None
    assert r.mention.target_provision_ref.statute_id == "celex:31996L0053"


def test_legacy_two_digit_year_directive_82_resolves() -> None:
    # Second legacy directive: "direktiivin 82/891/ETY 4 artiklan" → year 82 →
    # 1982, directive → L → 31982L0891.
    refs = recognize_eu_directive_refs("direktiivin 82/891/ETY 4 artiklan")
    assert len(refs) == 1
    r = refs[0]
    assert r.status is CiteConfidence.EXACT
    assert r.celex_candidates == ("31982L0891",)


def test_legacy_two_digit_year_decision_celex_letter_via_helper() -> None:
    # The CELEX type letter is supplied by the governing head word, so a 2-digit
    # year-first cite under a decision head resolves to the D type. ``päätös`` is
    # not a nickname head this lane scans for, so the head→type mapping is
    # exercised directly through the formal-cite helper: "85/432/ETY" under a
    # decision head → year 85 → 1985, D → 31985D0432.
    assert _celex_from_formal_cite("85/432/ETY", "päätöksen") == "31985D0432"
    # The same cite under a directive head takes the L type.
    assert _celex_from_formal_cite("85/432/ETY", "direktiivin") == "31985L0432"


def test_four_digit_year_directive_still_resolves() -> None:
    # Regression guard: the 4-digit year-first form must keep matching as a single
    # 4-digit year (never split "20" + "09/138"): "direktiivin 2009/138/EY".
    refs = recognize_eu_directive_refs("direktiivin 2009/138/EY 268 artiklan")
    assert len(refs) == 1
    assert refs[0].celex_candidates == ("32009L0138",)


def test_two_digit_slash_pair_without_eu_form_yields_nothing() -> None:
    # FP guard: a bare "NN/NNN" with no EU form marker is not a CELEX-bearing cite.
    # The head has no registry hit and the bare slash pair carries no /FORM suffix,
    # so nothing is emitted.
    refs = recognize_eu_directive_refs("direktiivin 96/53 3 artiklan")
    assert refs == []


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


# ---------------------------------------------------------------------------
# Intra-article element capture: kohta (paragraph/point) + alakohta (sub-point)
# ---------------------------------------------------------------------------


def _ref(text: str):
    refs = recognize_eu_directive_refs(text)
    assert len(refs) == 1, [
        r.mention.target_provision_ref.serialized()
        for r in refs
        if r.mention.target_provision_ref is not None
    ]
    return refs[0]


def _target_ref(result):
    target = result.mention.target_provision_ref
    assert target is not None
    return target


def test_kohta_carried_onto_subsection_num() -> None:
    # "N artiklan M kohdassa" — the EU kohta lands on subsection_num and renders
    # below the article, distinct from a bare-article cite.
    r = _ref("yleisen tietosuoja-asetuksen 6 artiklan 1 kohdassa tarkoitetulla tavalla")
    tgt = r.mention.target_provision_ref
    assert tgt is not None
    assert tgt.statute_id == "celex:32016R0679"
    assert tgt.section_label == "6"
    assert tgt.subsection_num == 1
    assert tgt.item_label is None
    assert tgt.serialized() == "celex:32016R0679/6/1"
    assert r.mention.surface_text == "6 artiklan 1 kohdassa"


def test_alakohta_carried_onto_item_label() -> None:
    # "N artiklan M kohdan L alakohdassa" — the lettered sub-point lands on
    # item_label and renders as the typed kLABEL segment.
    r = _ref("yleisen tietosuoja-asetuksen 6 artiklan 1 kohdan c alakohdassa")
    tgt = r.mention.target_provision_ref
    assert tgt is not None
    assert tgt.subsection_num == 1
    assert tgt.item_label == "c"
    assert tgt.serialized() == "celex:32016R0679/6/1/kc"
    assert r.mention.surface_text == "6 artiklan 1 kohdan c alakohdassa"


def test_kohta_serializes_distinctly_from_bare_article() -> None:
    # The whole point of the fix: the sub-element makes the serialized form
    # distinct from the article-only cite.
    bare = _ref("yleisen tietosuoja-asetuksen 6 artiklassa")
    with_kohta = _ref("yleisen tietosuoja-asetuksen 6 artiklan 1 kohdassa")
    assert _target_ref(bare).serialized() == "celex:32016R0679/6"
    assert (
        _target_ref(with_kohta).serialized()
        == "celex:32016R0679/6/1"
    )
    assert (
        _target_ref(bare).serialized()
        != _target_ref(with_kohta).serialized()
    )


def test_kohta_coordination_enumerates() -> None:
    # "N artiklan M ja K kohdassa" — kohta coordination enumerates one ref per
    # kohta, reusing the shared number-list grammar.
    refs = recognize_eu_directive_refs(
        "yleisen tietosuoja-asetuksen 7 artiklan 1 ja 2 kohdassa"
    )
    assert [_target_ref(r).serialized() for r in refs] == [
        "celex:32016R0679/7/1",
        "celex:32016R0679/7/2",
    ]
    # Surface spans the whole coordinated sub-element on each enumerated ref.
    assert all(r.mention.surface_text == "7 artiklan 1 ja 2 kohdassa" for r in refs)


def test_alakohta_coordination_enumerates() -> None:
    # "N artiklan M kohdan L ja P alakohdassa" — sub-point coordination
    # enumerates one ref per alakohta under the same kohta.
    refs = recognize_eu_directive_refs(
        "yleisen tietosuoja-asetuksen 18 artiklan 1 kohdan a ja b alakohdassa"
    )
    assert [_target_ref(r).serialized() for r in refs] == [
        "celex:32016R0679/18/1/ka",
        "celex:32016R0679/18/1/kb",
    ]


def test_article_coordination_with_shared_kohta() -> None:
    # "N ja K artiklan M kohdassa" — coordinated articles sharing one kohta:
    # cartesian product article × kohta.
    refs = recognize_eu_directive_refs(
        "yleisen tietosuoja-asetuksen 33 ja 35 artiklan 1 kohdassa"
    )
    assert [_target_ref(r).serialized() for r in refs] == [
        "celex:32016R0679/33/1",
        "celex:32016R0679/35/1",
    ]


def test_bare_article_no_kohta_unchanged() -> None:
    # A bare "N artiklassa" (locative, no genitive, no kohta) is untouched: no
    # subsection_num / item_label fabricated.
    r = _ref("teollisuuspäästödirektiivin 12 artiklassa")
    tgt = _target_ref(r)
    assert tgt.subsection_num is None
    assert tgt.item_label is None
    assert tgt.serialized() == "celex:32010L0075/12"
    assert r.mention.surface_text == "12 artiklassa"


def test_genitive_article_without_kohta_unchanged() -> None:
    # A genitive "N artiklan" NOT followed by a kohta tail must NOT fabricate a
    # sub-element (fail-loud): a trailing unrelated number is not a kohta.
    r = _ref("teollisuuspäästödirektiivin 12 artiklan mukaisesti")
    tgt = _target_ref(r)
    assert tgt.subsection_num is None
    assert tgt.item_label is None
    assert tgt.serialized() == "celex:32010L0075/12"


def test_alakohta_dash_range_not_fabricated() -> None:
    # A dash-range alakohta ("a–c alakohdassa") is NOT a coordination this lane
    # can soundly expand (letter-range expansion is unimplemented). It must NOT
    # enumerate as "a" + "c" (that would silently drop the middle "b"). Fail-loud:
    # keep the sound kohta level and leave the sub-point range uncaptured.
    refs = recognize_eu_directive_refs(
        "yleisen tietosuoja-asetuksen 6 artiklan 1 kohdan a–c alakohdassa"
    )
    assert [_target_ref(r).serialized() for r in refs] == [
        "celex:32016R0679/6/1",
    ]
    assert _target_ref(refs[0]).item_label is None


def test_kohta_resolves_via_formal_cite_head() -> None:
    # The intra-article tail also rides a bare-head-resolved-via-formal-cite ref:
    # "direktiivin 2013/11/EU 5 artiklan 2 kohdan a alakohdassa" → 32013L0011.
    r = _ref("direktiivin 2013/11/EU 5 artiklan 2 kohdan a alakohdassa")
    tgt = r.mention.target_provision_ref
    assert tgt.statute_id == "celex:32013L0011"
    assert tgt.serialized() == "celex:32013L0011/5/2/ka"
