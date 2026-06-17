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


def test_empty_text() -> None:
    assert recognize_by_name_refs("") == []
