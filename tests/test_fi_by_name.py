"""Tests for the by-name cross-statute reference recognizer.

Covers the ``[STATUTE_NAME_HEAD]`` family: cross-statute references made by
inflected statute NAME with no ``(NNN/YYYY)`` id anchor. See
``src/lawvm/finland/references/by_name.py``.
"""

from __future__ import annotations

from lawvm.core.reference_mention import CiteConfidence, CiteKind
from lawvm.finland.references.by_name import recognize_by_name_refs
from lawvm.finland.references.lemma_gate import GateVerdict, lemma_gate


def _gate_rejects(token: str, modifier: str | None = None) -> bool:
    """True iff the shared morphology gate hard-rejects ``token``.

    The by-name false-positive rejections are now owned by the M1-derived gate
    (:mod:`lawvm.finland.references.lemma_gate`); these helpers pin that the gate
    itself rejects each preserved FP family (not just that the recognizer emits
    nothing for some other incidental reason).
    """
    return (
        lemma_gate(token, peeled_modifier=modifier).verdict
        is GateVerdict.REJECT_KNOWN_OTHER
    )


def test_name_only_no_tail_emits_statute_level_mention() -> None:
    """``luonnonsuojelulaissa`` (no § tail) -> 1 STATUTE_ONLY cross-statute."""
    mentions = recognize_by_name_refs("luonnonsuojelulaissa säädetään tarkemmin.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"
    # Statute-level: no section path resolved.
    assert m.target_provision_ref.section_label == ""
    # The name surface is carried; no fabricated id. The span anchors the use
    # site in the text (offset 0, the full name word) — needed for the
    # defined-term binder's "binding precedes use" ordering check.
    assert m.surface_text == "luonnonsuojelulaissa"
    assert m.source_span is not None
    assert m.source_span.byte_offset == 0
    assert m.source_span.byte_len == len("luonnonsuojelulaissa")
    assert m.phrase_lemma == "statute_name_head"


def test_name_with_section_tail_carries_section_path() -> None:
    """``ympäristönsuojelulain 5 §:ssä`` -> 1 mention, section 5 + name."""
    mentions = recognize_by_name_refs("ympäristönsuojelulain 5 §:ssä säädetään.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojelulaki"
    assert m.target_provision_ref.section_label == "5"
    assert m.target_provision_ref.subsection_num is None


def test_name_with_momentti_tail() -> None:
    """A momentti tail carries through the shared body parser."""
    mentions = recognize_by_name_refs("työsopimuslain 7 §:n 2 momentissa säädetään.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:työsopimuslaki"
    assert m.target_provision_ref.section_label == "7"
    assert m.target_provision_ref.subsection_num == 2


def test_coordinated_compound_modifier_head_detected() -> None:
    """``maankäyttö- ja rakennuslain 132 §`` -> name head detected, section 132.

    The inflection rides the LAST conjunct's head (``rakennus`` + ``lain``), but
    the normalized key preserves the FULL coordinated compound name
    (``maankäyttö- ja rakennuslaki``), because the statute-name registry
    generates the coordinated surface under the whole name — keying on the
    truncated last conjunct alone (``rakennuslaki``) would miss the registered
    act. The reported surface includes the elided-head left conjunct.
    """
    mentions = recognize_by_name_refs("maankäyttö- ja rakennuslain 132 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:maankäyttö- ja rakennuslaki"
    assert m.target_provision_ref.section_label == "132"
    # Full coordinated name surface reported for overlay display.
    assert "maankäyttö- ja rakennuslain" in m.surface_text


def test_id_anchored_reference_emits_nothing() -> None:
    """``jätelain (646/2011) 3 §`` -> NOTHING (id-anchored; plain-text lane)."""
    assert recognize_by_name_refs("jätelain (646/2011) 3 § säädetään.") == []


def test_id_anchored_with_spacing_emits_nothing() -> None:
    """The id-paren exclusion tolerates whitespace before the paren."""
    assert recognize_by_name_refs("jätelain  ( 646/2011 ) 3 §") == []


def test_bare_section_ref_emits_nothing() -> None:
    """``5 §:ssä`` (no name head) -> NOTHING (internal / other lane)."""
    assert recognize_by_name_refs("5 §:ssä säädetään") == []
    assert recognize_by_name_refs("Tämän lain 5 §:ssä säädetään") == []


def test_nominative_bare_head_not_triggered() -> None:
    """An uninflected nominative head is not a by-name citation trigger."""
    assert recognize_by_name_refs("Tämä laki tulee voimaan.") == []
    assert recognize_by_name_refs("Annetaan asetus tarkemmista säännöksistä.") == []


def test_bare_governed_head_not_emitted() -> None:
    """A bare governed head (``valtioneuvoston asetuksessa``) is NOT emitted.

    There is no compound title — ``asetuksessa`` is a generic governed
    instrument, not a resolvable named act. The lane requires a compound title
    (modifier glued to the head), so this is correctly skipped.
    """
    assert (
        recognize_by_name_refs(
            "valtioneuvoston asetuksessa annetaan tarkempia säännöksiä"
        )
        == []
    )


def test_compound_asetus_head() -> None:
    """A compound ``...asetuksen`` head normalizes to its nominative compound."""
    mentions = recognize_by_name_refs("ympäristönsuojeluasetuksen 4 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojeluasetus"
    assert m.target_provision_ref.section_label == "4"


def test_section_coordination_expands_per_section() -> None:
    """A coordinated section list expands to one mention per section."""
    mentions = recognize_by_name_refs("rakennuslain 1 ja 3 §:ssä säädetään")
    sections = sorted(
        m.target_provision_ref.section_label
        for m in mentions
        if m.target_provision_ref is not None
    )
    assert sections == ["1", "3"]
    for m in mentions:
        assert m.target_provision_ref is not None
        assert m.target_provision_ref.statute_id == "fi-name:rakennuslaki"
        assert m.cite_confidence is CiteConfidence.STATUTE_ONLY


def test_multiple_distinct_names_in_one_text() -> None:
    """Two distinct by-name references in one text -> two mentions."""
    mentions = recognize_by_name_refs(
        "luonnonsuojelulaissa ja ympäristönsuojelulain 5 §:ssä säädetään."
    )
    names = sorted(
        m.target_provision_ref.statute_id
        for m in mentions
        if m.target_provision_ref is not None
    )
    assert names == [
        "fi-name:luonnonsuojelulaki",
        "fi-name:ympäristönsuojelulaki",
    ]


def test_paatos_compound_head_with_tail_inflected() -> None:
    """A compound ``päätös`` head (weak head) is recognised WHEN it has a tail.

    ``päätös`` is a productive common noun (``lupapäätöksen`` = permit decision),
    so the precision gate requires POSITIVE EVIDENCE that the token is a real act
    reference. A following provision tail (``4 §:ssä``) is that evidence, so the
    weak head still emits here (with vowel harmony preserved in the head form).
    """
    mentions = recognize_by_name_refs("ministeriön työllisyyspäätöksen 4 §:ssä määrätään")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:työllisyyspäätös"
    assert m.target_provision_ref.section_label == "4"


def test_weak_head_no_tail_lowercase_not_emitted() -> None:
    """A weak-head common noun with NO tail and a lowercase modifier -> NOTHING.

    ``työllisyyspäätöksessä`` (no § tail, lowercase) is an ordinary compound
    common noun, not an act reference. With no positive evidence (no provision
    tail, not capitalized mid-sentence) the precision gate suppresses it rather
    than emitting a non-reference. (Previously this over-fired.)
    """
    assert recognize_by_name_refs("ministeriön työllisyyspäätöksessä määrätään") == []


def test_no_partial_word_match() -> None:
    """The head must end a whole token; an embedded substring does not fire.

    ``lainata`` (to borrow) contains ``lain`` but is not a name head — the word
    boundary lookahead forbids a following name character.
    """
    assert recognize_by_name_refs("Voidaan lainata varoja toiselta.") == []


def test_weak_head_vuokrasopimus_no_tail_lowercase_not_emitted() -> None:
    """``vuokrasopimuksen`` (lease agreement; weak head, no tail, lowercase) -> NOTHING.

    ``sopimus`` is a productive common noun; ``vuokrasopimuksen`` is the genitive
    of "lease agreement", not an act title. No provision tail and a lowercase
    modifier mean no positive evidence, so the precision gate suppresses it.
    """
    assert recognize_by_name_refs("Osapuolet allekirjoittivat vuokrasopimuksen.") == []


def test_alainen_adjective_partitive_not_a_laki() -> None:
    """``sellaista`` / ``veronalaista`` are ``-alainen`` adjectives, NOT a ``laki``.

    The adjective partitive ``-laista`` is orthographically identical to the
    ``laki`` elative ``laista``, but an ``-alainen``/``-nainen`` adjective in the
    partitive is never a statute. These are hard-rejected (not even gated on
    evidence) because they are non-references.
    """
    assert recognize_by_name_refs("Tarkoitetaan sellaista toimintaa.") == []
    assert recognize_by_name_refs("kyseessä on veronalaista tuloa") == []
    # The M1-derived gate hard-rejects each adjective partitive.
    assert _gate_rejects("sellaista")
    assert _gate_rejects("veronalaista")


def test_real_laki_elative_with_tail_still_emitted() -> None:
    """``ympäristönsuojelulain 5 §:ssä`` (real act + provision tail) -> STILL emitted.

    A real act name with a provision tail carries the positive evidence the
    precision gate requires, so genuine references are preserved.
    """
    mentions = recognize_by_name_refs("ympäristönsuojelulain 5 §:ssä säädetään")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:ympäristönsuojelulaki"
    assert m.target_provision_ref.section_label == "5"


def test_capitalized_strong_head_still_emitted() -> None:
    """``Kuntalain`` (capitalized strong head ``laki``) -> still emitted.

    Strong heads keep the looser behavior; a capitalized strong-head title
    mid-sentence is a genuine by-name reference even without a provision tail.
    """
    mentions = recognize_by_name_refs("Sovelletaan Kuntalain mukaisesti.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:kuntalaki"


def test_pronoun_jollain_is_not_a_statute_name() -> None:
    """``jollain`` (jokin adessive, "by one of …") must not mis-segment as
    ``jol`` + ``lain`` (laki genitive) and invent ``fi-name:jollaki``."""
    assert recognize_by_name_refs("osoittaa jollain seuraavista yhdistelmistä") == []
    assert recognize_by_name_refs("ratkaistaan joillain tavoilla") == []
    assert _gate_rejects("jollain")
    assert _gate_rejects("joillain")


def test_las_agent_noun_plural_is_not_a_statute_name() -> None:
    """``oppilaille`` / ``sotilailta`` / ``kokelaiksi`` are ``-las``/``-läs`` agent
    nouns in the plural, NOT a ``laki`` head.

    The closed ``-las``/``-läs`` agent-noun class (``oppilas`` pupil, ``sotilas``
    soldier, ``kokelas`` cadet) forms its plural oblique on the stem ``-lai-``
    (``oppilaille`` "to pupils", ``sotilailta`` "from soldiers", ``kokelaiksi``
    "into cadets", ``oppilain`` "of pupils"). The trailing ``lai`` + case ending
    is byte-identical to a ``laki`` singular oblique, so the head trigger
    mis-segments it and invents ``fi-name:oppilaki``. These are non-references and
    are hard-rejected. A compound prefix (``rintamasotilaille``) is tolerated.
    """
    assert recognize_by_name_refs("opetus järjestetään oppilaille maksutta") == []
    assert recognize_by_name_refs("korvaus maksetaan rintamasotilaille") == []
    assert recognize_by_name_refs("peritään oppilailta lukukausimaksu") == []
    assert recognize_by_name_refs("annettu kokelaiksi otetuille") == []
    assert recognize_by_name_refs("etu kuuluu vain sotilailta perittyihin") == []
    assert recognize_by_name_refs("kaikkien oppilain oikeudet turvataan") == []
    for tok in ("oppilaille", "rintamasotilaille", "oppilailta", "kokelaiksi", "oppilain"):
        assert _gate_rejects(tok), tok


def test_laki_adessive_translative_collision_still_emitted() -> None:
    """The ``laki`` ADESSIVE/TRANSLATIVE (``eläkelailla`` / ``elintarvelaiksi``)
    is a REAL by-name citation and must NOT be swept by the agent-noun reject.

    ``eläkelailla`` ("by means of the pension act") and ``elintarvelaiksi``
    ("named the food act") share the ``-lai-`` surface fragment but carry a real
    statute modifier (``eläke`` / ``elintarve``), not an agent-noun stem. The
    agent-noun reject is anchored on the closed ``oppi``/``soti``/``koke`` heads,
    so these survive as genuine references.
    """
    elake = recognize_by_name_refs("oikeudet säädetään työeläkelailla tarkemmin")
    assert len(elake) == 1
    assert elake[0].target_provision_ref is not None
    assert elake[0].target_provision_ref.statute_id == "fi-name:työeläkelaki"
    elint = recognize_by_name_refs("päätöksessä sanotaan elintarvelaiksi se laki")
    assert len(elint) == 1
    assert elint[0].target_provision_ref is not None
    assert elint[0].target_provision_ref.statute_id == "fi-name:elintarvelaki"


def test_determiner_laki_collapse_is_not_a_statute_name() -> None:
    """A determiner glued to a ``laki`` oblique (elided space, OCR'd source) is a
    self-referential demonstrative, NOT a named act.

    ``tämänlain`` (``tämän lain`` "of this law"), ``tässälaissa`` (``tässä
    laissa`` "in this law"), ``mainitunlain`` (``mainitun lain`` "of the said
    law"). A real compound title's modifier is a noun stem, never an inflected
    determiner, so this collapse can never be a resolvable named act. Closed
    determiner set; hard-rejected.
    """
    assert recognize_by_name_refs("mikäli ei tässälaissa toisin säädetä") == []
    assert recognize_by_name_refs("vuoden kuluessa tämänlain voimaantulosta") == []
    assert recognize_by_name_refs("noudatetaan mainitunlain säännöksiä") == []
    assert _gate_rejects("tässälaissa", "tässä")
    assert _gate_rejects("tämänlain", "tämän")
    assert _gate_rejects("mainitunlain", "mainitun")


def test_real_la_stem_compound_law_still_emitted() -> None:
    """A real ``-la``-stem compound act (``maakuntalailla``) is NOT a false
    positive and must still emit.

    ``maakuntalailla`` ("by the regional-government act", adessive) is a genuine
    statute reference whose modifier (``maakunta``) is not an agent-noun head, so
    the agent-noun reject leaves it alone.
    """
    mentions = recognize_by_name_refs("toimivalta voidaan maakuntalailla siirtää")
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi-name:maakuntalaki"


def test_section_tail_surface_does_not_overcapture_following_prose() -> None:
    """The reported surface is the consumed name + § tail, NOT a fixed window.

    Previously the recognizer appended the whole 120-char tail window, so the
    surface ran on into the following prose (``... §:ssä tarkoitetun
    luontovahingon, aluehallintoviraston on sen lisäksi …``). It must stop at the
    bytes the section-tail grammar actually consumed.
    """
    text = (
        "luonnonsuojelulain 5 a §:ssä tarkoitetun luontovahingon, "
        "aluehallintoviraston on sen lisäksi ilmoitettava asiasta"
    )
    mentions = recognize_by_name_refs(text)
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:luonnonsuojelulaki"
    assert m.target_provision_ref.section_label == "5a"
    # The surface stops at the consumed provision tail — no run-on prose.
    assert m.surface_text == "luonnonsuojelulain 5 a §:ssä"


def test_section_tail_surface_trimmed_with_luku() -> None:
    """A luku-qualified § tail surface stops at ``§:ssä``, not the trailing prose."""
    mentions = recognize_by_name_refs(
        "osakeyhtiölain 13 luvun 9 §:ssä tarkoitetulla tavalla"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.surface_text == "osakeyhtiölain 13 luvun 9 §:ssä"


def test_jokin_pronoun_oblique_paradigm_not_a_statute_name() -> None:
    """The ``jokin`` paradigm ``joll-`` obliques must not mis-segment as ``laki``.

    Beyond the adessive ``jollain`` / ``joillain`` (already guarded), the further
    obliques ``jollaiksi`` / ``jollailla`` / ``jollaille`` / ``jollailta`` /
    ``jollaissa`` and their ``joilla-`` plurals mis-segment as a ``laki`` oblique
    and invent ``fi-name:jollaki``. These are pronouns, never statutes; the whole
    closed ``joll-`` oblique paradigm is hard-rejected.
    """
    for tok in (
        "jollaiksi",
        "jollailla",
        "jollaille",
        "jollailta",
        "jollaissa",
        "joillaiksi",
        "joillailla",
        "joillaille",
        "joillailta",
        "joillaissa",
    ):
        assert recognize_by_name_refs(f"Asia voidaan {tok} ratkaista.") == [], tok


def test_coordinated_compound_resolves_full_registry_key() -> None:
    """``perintö- ja lahjaverolain`` keys the FULL coordinated name.

    The registry generates the coordinated-compound surface under the whole name
    (``perintö- ja lahjaverolaki`` -> 1940/378). Keying on the truncated last
    conjunct (``lahjaverolaki``) would silently under-resolve, so the ``fi-name:``
    key must carry the full coordinated compound.
    """
    mentions = recognize_by_name_refs("perintö- ja lahjaverolain 9 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert (
        m.target_provision_ref.statute_id == "fi-name:perintö- ja lahjaverolaki"
    )
    assert m.target_provision_ref.section_label == "9"
    # No-tail coordinated form keys the full name too.
    no_tail = recognize_by_name_refs("perintö- ja lahjaverolain mukaan")
    assert len(no_tail) == 1
    assert no_tail[0].target_provision_ref is not None
    assert (
        no_tail[0].target_provision_ref.statute_id
        == "fi-name:perintö- ja lahjaverolaki"
    )


def test_kapitalisaatiosopimus_is_not_a_statute_name() -> None:
    """``kapitalisaatiosopimus`` (an insurance/investment product) -> NOTHING.

    A capitalization agreement is a CONTRACT/PRODUCT common noun, not a named act.
    Its ``sopimus``-head obliques fire the by-name trigger, and the weak-head
    evidence gate previously let it through on BOTH FP paths it actually takes in
    the corpus (tuloverolaki 1992/1535): capitalized mid-sentence (a defined-term
    surface), and with a ``§`` provision tail (the sections are tuloverolaki's
    own, not an act called ``kapitalisaatiosopimus``). It is now hard-rejected by
    the negative-paradigm gate, so neither path emits.
    """
    # Capitalized mid-sentence (defined-term shape, previously emitted).
    assert (
        recognize_by_name_refs(
            "Verovelvollisen Kapitalisaatiosopimuksen tuotto on veronalaista."
        )
        == []
    )
    # With a provision tail (previously emitted via has_provision_tail evidence).
    assert recognize_by_name_refs("kapitalisaatiosopimuksen 46 ja 47 §:n mukaan") == []
    # Plain lowercase oblique.
    assert recognize_by_name_refs("Sopimuksena pidetään kapitalisaatiosopimusta.") == []
    # The gate itself hard-rejects every oblique surface.
    assert _gate_rejects("kapitalisaatiosopimuksen")
    assert _gate_rejects("Kapitalisaatiosopimuksen")
    assert _gate_rejects("kapitalisaatiosopimuksesta")
    assert _gate_rejects("kapitalisaatiosopimukselle")


def test_genuine_sopimuslaki_act_still_resolves() -> None:
    """A real ``...sopimuslaki`` act is NOT over-broadened away by the FP filter.

    ``vakuutussopimuslaki`` carries the ``laki`` head (``vakuutussopimuslain``),
    a different head from the rejected ``kapitalisaatiosopimus`` (the ``sopimus``
    head, ``kapitalisaatiosopimuksen``). The two never collide, so suppressing the
    product noun must leave the named act untouched.
    """
    mentions = recognize_by_name_refs("vakuutussopimuslain 2 §:ssä säädetään.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:vakuutussopimuslaki"
    assert m.target_provision_ref.section_label == "2"
    # The gate must not reject the genuine sopimuslaki obliques.
    assert not _gate_rejects("vakuutussopimuslain")
    assert not _gate_rejects("vakuutussopimuslaissa")
    assert not _gate_rejects("työsopimuslaissa")


def test_kauppalain_market_town_homonym_no_tail_not_emitted() -> None:
    """``kauppalain`` coordinated with municipality terms (no § tail) -> NOTHING.

    ``kauppalain`` is the archaic plural genitive of ``kauppala`` (a market town,
    a municipality type) AND the genitive singular of ``kauppalaki`` (the Sale of
    Goods Act, 355/1987). In statute 1964/639 the road-law clause
    ``maalaiskuntien, kauppalain tai kaupunkien`` is the market-town reading. With
    no provision tail and a lowercase mid-sentence modifier there is no positive
    evidence of an act citation, so the named-homonym precision gate suppresses it
    rather than mis-resolving to ``fi-name:kauppalaki``.
    """
    assert (
        recognize_by_name_refs(
            "Ne maalaiskuntien, kauppalain tai kaupunkien alueilla olevat tiet."
        )
        == []
    )
    assert (
        recognize_by_name_refs(
            "kun on kysymys kaupunkien ja eri kuntina olevien kauppalain alueella."
        )
        == []
    )


def test_genuine_kauppalaki_with_section_tail_still_resolves() -> None:
    """A genuine ``kauppalain N §`` reference is preserved by the homonym gate.

    The named-homonym gate only suppresses ``kauppalain`` when there is NO
    positive evidence. A following ``§`` tail (``kauppalain 41 §``) is the
    citation shape every genuine corpus Sale-of-Goods reference carries, so it
    still resolves to ``fi-name:kauppalaki`` with the section path.
    """
    mentions = recognize_by_name_refs("sekä kauppalain 41 §:n mukaisesti.")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:kauppalaki"
    assert m.target_provision_ref.section_label == "41"

    coord = recognize_by_name_refs(
        "ei sovelleta mitä kauppalain 13 §:n 3 momentissa säädetään."
    )
    assert len(coord) >= 1
    assert all(
        m.target_provision_ref is not None
        and m.target_provision_ref.statute_id == "fi-name:kauppalaki"
        for m in coord
    )


def test_kauppalaki_unambiguous_inessive_not_over_suppressed() -> None:
    """Only the ``lain`` form is homonymous; ``kauppalaissa`` is unambiguous.

    The homonym is keyed on the exact ``(name, oblique)`` pair: ``kauppala``'s
    plural inessive/elative are ``kauppaloissa`` / ``kauppaloista``, never
    ``kauppalaissa`` / ``kauppalaista``. So the inessive ``kauppalaissa`` can only
    be ``kauppalaki`` and must still resolve at statute level with no tail — the
    homonym gate must not over-broaden to every ``kauppala-`` surface.
    """
    mentions = recognize_by_name_refs("mitä kauppalaissa säädetään sovelletaan.")
    assert len(mentions) == 1
    assert mentions[0].target_provision_ref is not None
    assert mentions[0].target_provision_ref.statute_id == "fi-name:kauppalaki"


def test_empty_text() -> None:
    assert recognize_by_name_refs("") == []


# ---------------------------------------------------------------------------
# G1 — chapter-organized by-name refs carry the chapter onto the AKN path
# ---------------------------------------------------------------------------


def test_g1_chapter_qualified_by_name_carries_provision_path() -> None:
    """``rikoslain 47 luvun 4 §`` carries chapter 47 onto the AKN provision path.

    Without it the chapter is dropped and the target collapses onto ``rikoslain
    4 §`` — but §4 exists in EVERY rikoslaki chapter, so the chapter-47 cite would
    point at chapter 1. The chapter rides the ``provision_path`` via the SAME
    ``chp_N__sec_M`` form the parenthetical / plain-text lane uses.
    """
    mentions = recognize_by_name_refs("rikoslain 47 luvun 4 §:n mukaan")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:rikoslaki"
    assert m.target_provision_ref.provision_path == "chp_47__sec_4"
    assert m.target_provision_ref.section_label == "4"


def test_g1_chapter_qualified_distinct_from_bare_section() -> None:
    """``rikoslain 47 luvun 4 §`` and ``rikoslain 4 §`` must NOT share a target."""
    chaptered = recognize_by_name_refs("rikoslain 47 luvun 4 §:n")
    bare = recognize_by_name_refs("rikoslain 4 §")
    assert len(chaptered) == 1 and len(bare) == 1
    ct, bt = chaptered[0].target_provision_ref, bare[0].target_provision_ref
    assert ct is not None and bt is not None
    # Same statute, same section label — but the chapter-qualified path keeps them
    # distinct (the chapter-less bare cite has no chapter path).
    assert ct.statute_id == bt.statute_id == "fi-name:rikoslaki"
    assert ct.section_label == bt.section_label == "4"
    assert ct.provision_path == "chp_47__sec_4"
    assert bt.provision_path == ""
    assert ct.provision_path != bt.provision_path


def test_g1_chapter_with_momentti_and_kohta_enumeration() -> None:
    """``osakeyhtiölain 20 luvun 4 §:n 1 momentin 1–3 kohdassa`` keeps the chapter
    on every enumerated kohta target."""
    mentions = recognize_by_name_refs(
        "osakeyhtiölain 20 luvun 4 §:n 1 momentin 1–3 kohdassa"
    )
    assert len(mentions) == 3
    for m, item in zip(mentions, ("1", "2", "3"), strict=True):
        assert m.target_provision_ref is not None
        assert m.target_provision_ref.statute_id == "fi-name:osakeyhtiölaki"
        assert m.target_provision_ref.provision_path == "chp_20__sec_4"
        assert m.target_provision_ref.section_label == "4"
        assert m.target_provision_ref.subsection_num == 1
        assert m.target_provision_ref.item_label == item


# ---------------------------------------------------------------------------
# G2 — descriptive-participle ``[X:stä] annetun lain N §`` citations
# ---------------------------------------------------------------------------


def test_g2_descriptive_participle_emits_ref_with_section() -> None:
    """``valvotusta koevapaudesta annetun lain 23 §:n 1 momentissa`` -> one
    cross-statute mention keyed head-first as the official-title surface."""
    mentions = recognize_by_name_refs(
        "valvotusta koevapaudesta annetun lain 23 §:n 1 momentissa"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.phrase_lemma == "statute_name_descriptive_participle"
    assert m.target_provision_ref is not None
    # Head-first ``laki <complement>`` = the registry's official-title surface for
    # 629/2013 ("Laki valvotusta koevapaudesta"), so resolve.py can resolve it.
    assert m.target_provision_ref.statute_id == "fi-name:laki valvotusta koevapaudesta"
    assert m.target_provision_ref.section_label == "23"
    assert m.target_provision_ref.subsection_num == 1


def test_g2_descriptive_complement_does_not_overcapture_preceding_clause() -> None:
    """The complement anchors on the elative head, not a greedy left run.

    ``… sitä luottolaitostoiminnasta annetun lain 16 §`` keeps only
    ``luottolaitostoiminnasta`` — the determiner ``sitä`` and the rest of the
    preceding clause are NOT swallowed into the title key.
    """
    mentions = recognize_by_name_refs(
        "konsolidointiryhmällä sitä luottolaitostoiminnasta annetun lain 16 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:laki luottolaitostoiminnasta"
    assert m.target_provision_ref.section_label == "16"


def test_g2_descriptive_premodifier_stops_at_negative_verb() -> None:
    """A bare-partitive verb before the elative head is not a title pre-modifier.

    ``ei sovelleta osuuspankeista annetun lain 5 §`` (shape of 2014/697): the
    negative verb ``sovelleta`` ends ``-ta`` but NOT ``-sta``, so it does not
    agree with the elative head ``osuuspankeista`` and must not be swallowed.
    """
    mentions = recognize_by_name_refs(
        "Tätä lakia ei sovelleta osuuspankeista annetun lain 5 §:ssä tarkoitettuun"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:laki osuuspankeista"
    assert m.target_provision_ref.section_label == "5"
    assert m.surface_text == "osuuspankeista annetun lain 5 §:ssä"


def test_g2_descriptive_premodifier_stops_at_inessive_locative() -> None:
    """An inessive locative + joiner from the prior clause is not a pre-modifier.

    ``1 momentissa tai luottolaitostoiminnasta annetun lain 3 §`` (shape of
    2014/1194): ``momentissa`` is inessive (``-ssa``), which does not pre-modify
    an elative head, and the coordinator ``tai`` belongs to the prior clause.
    Only ``luottolaitostoiminnasta`` survives in the title key.
    """
    mentions = recognize_by_name_refs(
        "Edellä 1 momentissa tai luottolaitostoiminnasta annetun lain 3 §:ssä tarkoitettua"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:laki luottolaitostoiminnasta"
    assert m.target_provision_ref.section_label == "3"
    assert m.surface_text == "luottolaitostoiminnasta annetun lain 3 §:ssä"


def test_g2_descriptive_participle_premodified_title_kept_in_full() -> None:
    """A genuine ``[complement] [elative participle] [elative head]`` title is NOT
    over-trimmed.

    ``hakukoneita koskevasta kieltomenettelystä`` — ``hakukoneita`` is partitive
    (``-ta``), not elative, but it is the complement of the immediately-following
    elative attributive participle ``koskevasta``, so it is a genuine title member
    and must be kept (tightening the clause-pollution must not eat real titles).
    A longer locative modifier chain before the participle is likewise preserved.
    """
    one = recognize_by_name_refs(
        "hakukoneita koskevasta kieltomenettelystä annetun lain 2 §:ssä"
    )
    assert len(one) == 1
    assert one[0].target_provision_ref is not None
    assert (
        one[0].target_provision_ref.statute_id
        == "fi-name:laki hakukoneita koskevasta kieltomenettelystä"
    )

    chain = recognize_by_name_refs(
        "neuvoa-antavissa kunnallisissa kansanäänestyksissä noudatettavasta "
        "menettelystä annetun lain 1 §:ssä"
    )
    assert len(chain) == 1
    assert chain[0].target_provision_ref is not None
    assert chain[0].target_provision_ref.statute_id == (
        "fi-name:laki neuvoa-antavissa kunnallisissa kansanäänestyksissä "
        "noudatettavasta menettelystä"
    )


def test_g2_descriptive_internal_coordination_kept_clause_coordinator_dropped() -> None:
    """A coordinator joining two elative title members is kept; a coordinator
    joining the citation to the PRIOR clause is dropped.

    ``julkisista hankinnoista ja käyttöoikeussopimuksista`` — both sides elative,
    so the whole coordinated title survives. ``eläkekassa tai lisäeläkesäätiöistä
    …`` — ``eläkekassa`` is not an elative title member, so the clause coordinator
    ``tai`` and everything left of it is dropped.
    """
    kept = recognize_by_name_refs(
        "julkisista hankinnoista ja käyttöoikeussopimuksista annetun lain 5 §:ssä"
    )
    assert len(kept) == 1
    assert kept[0].target_provision_ref is not None
    assert kept[0].target_provision_ref.statute_id == (
        "fi-name:laki julkisista hankinnoista ja käyttöoikeussopimuksista"
    )

    dropped = recognize_by_name_refs(
        "eläkekassa tai lisäeläkesäätiöistä ja lisäeläkekassoista annetun lain 1 §:ssä"
    )
    assert len(dropped) == 1
    assert dropped[0].target_provision_ref is not None
    assert dropped[0].target_provision_ref.statute_id == (
        "fi-name:laki lisäeläkesäätiöistä ja lisäeläkekassoista"
    )


def test_g2_descriptive_two_citations_one_sentence() -> None:
    """Two descriptive citations in one sentence yield two clean, distinct nodes."""
    mentions = recognize_by_name_refs(
        "luottolaitostoiminnasta annetun lain 16 §:ssä tai "
        "sijoituspalveluyrityksistä annetun lain 10 §:ssä tarkoitettua"
    )
    keys = {
        (m.target_provision_ref.statute_id, m.target_provision_ref.section_label)
        for m in mentions
        if m.target_provision_ref is not None
    }
    assert keys == {
        ("fi-name:laki luottolaitostoiminnasta", "16"),
        ("fi-name:laki sijoituspalveluyrityksistä", "10"),
    }


def test_g2_nojalla_authority_basis_not_double_emitted() -> None:
    """``… annetun lain [N §:n] nojalla`` is the ISSUED_UNDER path's — not ours."""
    assert recognize_by_name_refs("eläimistä annetun lain 6 §:n nojalla määrätään") == []
    assert recognize_by_name_refs("eläimistä annetun lain nojalla annetaan") == []


def test_g2_inline_id_owned_by_plain_text_lane() -> None:
    """``… annetun lain (NNN/YYYY) N §`` is the plain-text by-id lane's case."""
    assert (
        recognize_by_name_refs(
            "sijoituspalveluyrityksistä annetun lain (922/2007) 46 §:n 2 momentissa"
        )
        == []
    )


def test_g2_non_citation_annetun_lain_fragment_emits_nothing() -> None:
    """A non-citation ``annetun lain`` fragment (no elative descriptive title)."""
    # No descriptive elative complement before ``annetun``.
    assert recognize_by_name_refs("on annetun lain perusteella ratkaistava") == []
    assert recognize_by_name_refs("tämän lain mukaan annetun lain 5 §") == []


def test_g2_intervening_date_phrase_stripped() -> None:
    """An enactment date phrase between the title and ``annetun`` is not part of
    the title key but stays in the recorded surface."""
    mentions = recognize_by_name_refs(
        "kielitaidosta 1 päivänä kesäkuuta 1922 annetun lain 6 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:laki kielitaidosta"
    assert m.target_provision_ref.section_label == "6"


def test_g2_genitive_premodifier_chain_kept_in_full() -> None:
    """Genitive (``-n``) premodifiers stacked on the elative head are part of the
    title NP and must be recovered, not truncated to the bare elative head.

    Finnish statute titles stack genitive premodifiers on the elative subject
    (``Laki sähköisen viestinnän palveluista``); the elative-only left walk
    previously dropped them, degrading the key to over-broad ``laki palveluista``.
    """
    # Two-genitive chain (witness 2019/587 / Laki sähköisen viestinnän palveluista).
    two = recognize_by_name_refs(
        "sähköisen viestinnän palveluista annetun lain 3 §:ssä"
    )
    assert len(two) == 1
    assert two[0].target_provision_ref is not None
    assert (
        two[0].target_provision_ref.statute_id
        == "fi-name:laki sähköisen viestinnän palveluista"
    )

    # Plural genitive ``-ten`` + singular genitive (Laki viranomaisten toiminnan
    # julkisuudesta).
    pub = recognize_by_name_refs(
        "viranomaisten toiminnan julkisuudesta annetun lain 24 §:ssä"
    )
    assert len(pub) == 1
    assert pub[0].target_provision_ref is not None
    assert (
        pub[0].target_provision_ref.statute_id
        == "fi-name:laki viranomaisten toiminnan julkisuudesta"
    )

    # Single genitive premodifier (Laki terveydenhuollon asiakasmaksuista).
    amk = recognize_by_name_refs(
        "terveydenhuollon asiakasmaksuista annetun lain 1 §:ssä"
    )
    assert len(amk) == 1
    assert amk[0].target_provision_ref is not None
    assert (
        amk[0].target_provision_ref.statute_id
        == "fi-name:laki terveydenhuollon asiakasmaksuista"
    )

    # Single genitive premodifier (Laki yksityishenkilön velkajärjestelystä).
    velka = recognize_by_name_refs(
        "yksityishenkilön velkajärjestelystä annetun lain 30 §:ssä"
    )
    assert len(velka) == 1
    assert velka[0].target_provision_ref is not None
    assert (
        velka[0].target_provision_ref.statute_id
        == "fi-name:laki yksityishenkilön velkajärjestelystä"
    )


def test_g2_genitive_premodifier_stops_at_verb_n_form() -> None:
    """A clause verb ending in ``-n`` is the dominant prior-clause polluter and
    must NOT be chained as a genitive premodifier.

    ``… säädetään moottoriajoneuvoverosta annetun lain …`` — the passive present
    ``säädetään`` ends ``-n`` but is a verb, not a genitive noun. The walk stops
    at it; only the elative head survives.
    """
    mentions = recognize_by_name_refs(
        "verosta säädetään moottoriajoneuvoverosta annetun lain 3 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    # ``säädetään`` is NOT swallowed — passive verb, not a genitive premodifier.
    assert m.target_provision_ref.statute_id == "fi-name:laki moottoriajoneuvoverosta"


def test_g2_genitive_premodifier_stops_at_function_word_n() -> None:
    """A ``-n``-final function word (``siten``/``kuin``) from the prior clause is
    connective tissue, not a title premodifier — the walk stops at it."""
    mentions = recognize_by_name_refs(
        "korvataan siten kuin sijoituspalveluyrityksistä annetun lain 1 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    # Neither ``kuin`` nor ``siten`` is chained.
    assert m.target_provision_ref.statute_id == "fi-name:laki sijoituspalveluyrityksistä"


def test_g2_genitive_premodifier_chain_capped() -> None:
    """The genitive chain is capped so a stranded prior-clause total object (a
    genuine genitive noun) cannot be chained past the title's own premodifiers.

    ``antaa luvan sähköisen viestinnän palveluista annetun lain …`` — ``luvan``
    is the verb ``antaa``'s nominal total object (genitive ``-n``), but it sits
    THIRD in the ``-n`` run behind the two genuine title premodifiers
    (``sähköisen viestinnän``), so the cap (2) refuses it. The genuine 2-genitive
    title is recovered in full; the prior-clause object is left out.
    """
    mentions = recognize_by_name_refs(
        "viranomainen voi antaa luvan sähköisen viestinnän palveluista "
        "annetun lain 3 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    # Full 2-genitive title kept; ``luvan`` beyond the cap is NOT swallowed.
    assert (
        m.target_provision_ref.statute_id
        == "fi-name:laki sähköisen viestinnän palveluista"
    )


def test_g2_genitive_premodifier_stops_at_determiner() -> None:
    """A determiner (``sen``/``tämän``) ending in ``-n`` before the genitive
    premodifier chain is a stopword, not a title member — the walk stops at it."""
    mentions = recognize_by_name_refs(
        "noudatetaan sen terveydenhuollon asiakasmaksuista annetun lain 1 §:ssä"
    )
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    # ``sen`` (determiner stopword) is not chained; chain stops after it.
    assert (
        m.target_provision_ref.statute_id
        == "fi-name:laki terveydenhuollon asiakasmaksuista"
    )


# ── -kaari (code) heads: oikeudenkäymiskaari, maakaari, … (gap [2]) ──────────
#
# The historical Finnish CODES are statutes named by inflected title exactly like
# -laki acts, but ``kaari`` is not an M1 statute head, so the bare-head lane never
# fired on them. These must be recognized as CROSS-STATUTE by-name anchors (codes
# ARE statutes), chapter-qualified, resolving via the statute-name registry — and
# NEVER leaking as internal self-references to the citing statute.


def test_kaari_head_chapter_qualified_cross_statute() -> None:
    """``oikeudenkäymiskaaren 12 luvun 32 §:ää`` -> chapter-qualified cross-statute."""
    mentions = recognize_by_name_refs("oikeudenkäymiskaaren 12 luvun 32 §:ää")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.cite_confidence is CiteConfidence.STATUTE_ONLY
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:oikeudenkäymiskaari"
    assert m.target_provision_ref.provision_path == "chp_12__sec_32"
    assert m.target_provision_ref.section_label == "32"


def test_kaari_head_inessive_section() -> None:
    """``oikeudenkäymiskaaren 17 luvun 65 §:ssä`` -> chp_17__sec_65 cross-statute."""
    mentions = recognize_by_name_refs("oikeudenkäymiskaaren 17 luvun 65 §:ssä")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.cite_kind is CiteKind.CROSS_STATUTE
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:oikeudenkäymiskaari"
    assert m.target_provision_ref.provision_path == "chp_17__sec_65"


def test_kaari_other_codes_resolve_by_name() -> None:
    """maakaari / kauppakaari / perintökaari are recognized as cross-statute codes."""
    for text, key, path in (
        ("maakaaren 2 luvun 1 §:n mukaan", "fi-name:maakaari", "chp_2__sec_1"),
        ("kauppakaaren 10 luvun 8 §", "fi-name:kauppakaari", "chp_10__sec_8"),
        ("perintökaaren 5 luvun 2 §:ssä", "fi-name:perintökaari", "chp_5__sec_2"),
    ):
        mentions = recognize_by_name_refs(text)
        assert len(mentions) == 1, text
        m = mentions[0]
        assert m.cite_kind is CiteKind.CROSS_STATUTE
        assert m.target_provision_ref is not None
        assert m.target_provision_ref.statute_id == key
        assert m.target_provision_ref.provision_path == path


def test_kaari_head_no_tail_statute_level() -> None:
    """A bare ``maakaaressa`` (no § tail) -> one statute-level cross-statute ref."""
    mentions = recognize_by_name_refs("maakaaressa säädetään")
    assert len(mentions) == 1
    m = mentions[0]
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "fi-name:maakaari"
    assert m.target_provision_ref.section_label == ""


def test_kaari_id_anchored_form_deferred_to_plaintext_lane() -> None:
    """``oikeudenkäymiskaaren (4/1734) 12 luvun 32 §`` is the by-id lane's case."""
    assert recognize_by_name_refs("oikeudenkäymiskaaren (4/1734) 12 luvun 32 §") == []


def test_kaari_bare_head_no_modifier_not_emitted() -> None:
    """A bare inflected ``kaaressa`` with no compound modifier is not a title."""
    assert recognize_by_name_refs("kaaressa todetaan") == []
