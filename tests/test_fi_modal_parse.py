"""Tests for the modal-predicate / actor_modal construction parse + census.

Mirrors ``tests/test_fi_temporal_parse.py`` discipline: IR + projection + total
token ownership + the four deontic kinds + polarity + active/passive voice +
census classification on hand-built witnesses. The witnesses use real momentit
(the family survey cites 2009/945 and 2006/1338) plus minimal synthetic shapes
isolating each dimension.

The parse is SURFACE-ONLY and ADDITIVE; these tests assert the construction-grammar
contract (closed cue list, kind classification, no silent drop, oracle-comparable
projection key), NOT any production behaviour change.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.modal_census import (
    _modal_oracle_keys_for_span,
    _modal_segment_selector,
)
from lawvm.finland.legal_surface.family_census import classify
from lawvm.finland.legal_surface.modal_parse import (
    KIND_OBLIGATION,
    KIND_PERMISSION,
    KIND_POWER,
    KIND_PROHIBITION,
    MODAL_LANE_CONSTRUCTION_OWNED,
    MODAL_LANE_DECLINED,
    POLARITY_AFFIRMATIVE,
    POLARITY_NEGATIVE,
    VOICE_ACTIVE,
    VOICE_PASSIVE,
    assert_total_ownership,
    modal_key,
    parse_modal_sentence,
    projection_modal_keys,
)


# ---------------------------------------------------------------------------
# IR + total token ownership
# ---------------------------------------------------------------------------


def _check_total(text: str) -> None:
    mp = parse_modal_sentence(text)
    assert_total_ownership(mp)


def test_total_ownership_holds_on_each_kind() -> None:
    for text in (
        "Viranomaisen on tehtävä päätös viipymättä.",
        "Hakija saa hakea muutosta päätökseen.",
        "Viranomainen ei saa luovuttaa tietoja sivulliselle.",
        "Asetuksella säädetään tarkemmin menettelystä.",
        "Työnantaja on velvollinen järjestämään työterveyshuollon.",
    ):
        _check_total(text)


def test_total_ownership_partitions_exactly() -> None:
    text = "Viranomaisen on tehtävä päätös viipymättä."
    mp = parse_modal_sentence(text)
    # Every char is owned by exactly one of: a core's cue/addressee/object span,
    # or a residual. The postcondition asserts no gap; here we also assert the
    # residual list is well-typed and the union covers the whole span.
    n = len(text)
    covered = [False] * n
    for c in mp.cores:
        for s, e in (
            (c.cue_start, c.cue_end),
            (c.addressee_start, c.addressee_end),
            (c.object_start, c.object_end),
        ):
            if s is None or e is None:
                continue
            for i in range(s, e):
                covered[i] = True
    for r in mp.residuals:
        for i in range(r.char_start, r.char_end):
            covered[i] = True
    assert all(covered)


# ---------------------------------------------------------------------------
# The four deontic kinds
# ---------------------------------------------------------------------------


def test_obligation_necessive_on() -> None:
    mp = parse_modal_sentence("Viranomaisen on tehtävä päätös viipymättä.")
    assert mp.kind == "modal"
    assert mp.parser_lane == MODAL_LANE_CONSTRUCTION_OWNED
    (core,) = mp.cores
    assert core.kind == KIND_OBLIGATION
    assert core.cue == "on"
    assert core.polarity == POLARITY_AFFIRMATIVE
    assert core.voice == VOICE_ACTIVE


def test_obligation_on_velvollinen() -> None:
    mp = parse_modal_sentence("Työnantaja on velvollinen järjestämään työterveyshuollon.")
    (core,) = mp.cores
    assert core.kind == KIND_OBLIGATION
    assert core.cue == "on velvollinen"


def test_permission_saa() -> None:
    mp = parse_modal_sentence("Hakija saa hakea muutosta päätökseen.")
    (core,) = mp.cores
    assert core.kind == KIND_PERMISSION
    assert core.cue == "saa"
    assert core.polarity == POLARITY_AFFIRMATIVE


def test_power_passive_saadetaan() -> None:
    mp = parse_modal_sentence("Asetuksella säädetään tarkemmin menettelystä.")
    (core,) = mp.cores
    assert core.kind == KIND_POWER
    assert core.cue == "säädetään"
    assert core.voice == VOICE_PASSIVE


def test_prohibition_ei_saa() -> None:
    mp = parse_modal_sentence("Viranomainen ei saa luovuttaa tietoja sivulliselle.")
    (core,) = mp.cores
    assert core.kind == KIND_PROHIBITION
    assert core.cue == "ei saa"
    assert core.polarity == POLARITY_NEGATIVE
    assert core.voice == VOICE_ACTIVE


def test_obligation_tulee_necessive_still_fires() -> None:
    # The necessive obligation ``X:n tulee tehdä`` must still produce a core.
    mp = parse_modal_sentence("Hakijan tulee toimittaa selvitys viipymättä.")
    (core,) = mp.cores
    assert core.kind == KIND_OBLIGATION
    assert core.cue == "tulee"


def test_tulee_voimaan_commencement_not_obligation() -> None:
    # ``tulee voimaan`` is the temporal come-into-force idiom (owned by the
    # temporal island), NOT a deontic obligation — it must be gated out so the
    # commencement formula is not mis-keyed as a modal core.
    mp = parse_modal_sentence("Tämä laki tulee voimaan 1 päivänä tammikuuta 2016.")
    assert all(c.cue != "tulee" for c in mp.cores)
    assert mp.kind == "declined"


# ---------------------------------------------------------------------------
# Polarity + voice are first-class
# ---------------------------------------------------------------------------


def test_negated_permission_is_prohibition() -> None:
    # ``ei saa`` is mapped to prohibition directly; assert the polarity dimension
    # is carried and the kind refinement holds for any negative permission/power.
    mp = parse_modal_sentence("Hakija ei saa luovuttaa asiakirjaa.")
    (core,) = mp.cores
    assert core.polarity == POLARITY_NEGATIVE
    assert core.kind == KIND_PROHIBITION


def test_passive_voice_marker() -> None:
    mp = parse_modal_sentence("Tarkemmista säännöksistä voidaan säätää asetuksella.")
    (core,) = mp.cores
    assert core.voice == VOICE_PASSIVE
    assert core.cue == "voidaan"


def test_active_voice_marker() -> None:
    mp = parse_modal_sentence("Viranomainen voi pyytää lisäselvitystä.")
    (core,) = mp.cores
    assert core.voice == VOICE_ACTIVE


# ---------------------------------------------------------------------------
# Addressee: overt subject vs underspecified (impersonal/passive register)
# ---------------------------------------------------------------------------


def test_overt_subject_addressee_span() -> None:
    text = "Viranomainen voi pyytää lisäselvitystä."
    mp = parse_modal_sentence(text)
    (core,) = mp.cores
    assert core.addressee_underspecified is False
    assert core.addressee_start is not None and core.addressee_end is not None
    # The captured subject span is the surface NP before the cue.
    assert "Viranomainen" in text[core.addressee_start : core.addressee_end]


def test_passive_subjectless_is_underspecified() -> None:
    # Sentence-initial impersonal passive with no overt subject NP before the cue.
    text = "Säädetään tarkemmin menettelystä."
    mp = parse_modal_sentence(text)
    (core,) = mp.cores
    assert core.voice == VOICE_PASSIVE
    assert core.addressee_underspecified is True
    assert core.addressee_start is None


def test_reference_inessive_suffix_is_not_a_subject() -> None:
    # ``… 69 d–69 g §:ssä säädetään`` — the inessive ending of a § reference glued
    # to the reference colon (``§:ssä``) must NOT be mis-read as the modal frame's
    # subject NP (the body class admits ``:``, so the suffix ``ssä`` walked back to
    # the ``§`` and leaked in as a bogus subject, fragmenting the reference leaf in
    # the source-syntax forest). The passive ``säädetään`` is impersonal here.
    text = "sovelletaan mitä 69 d–69 g §:ssä säädetään."
    mp = parse_modal_sentence(text)
    cores = [c for c in mp.cores if c.cue == "säädetään"]
    assert cores, "the passive provision verb cue must still fire"
    (core,) = cores
    assert core.voice == VOICE_PASSIVE
    assert core.addressee_underspecified is True
    assert core.addressee_start is None
    # No modal span may overlap the reference inessive ending ``§:ssä``.
    ssa = text.index("§:ssä")
    for c in mp.cores:
        for s, e in (
            (c.addressee_start, c.addressee_end),
            (c.object_start, c.object_end),
        ):
            if s is None or e is None:
                continue
            assert not (s < ssa + len("§:ssä") and e > ssa), (
                f"modal span ({s},{e}) overlaps the reference ending §:ssä"
            )


# ---------------------------------------------------------------------------
# Bare ``on`` necessive gate (copula must NOT fire)
# ---------------------------------------------------------------------------


def test_bare_on_copula_declines() -> None:
    # A plain copula ("X on Y") is NOT a deontic modal and must not fire a core.
    mp = parse_modal_sentence("Päätös on lainvoimainen.")
    assert mp.cores == ()
    assert mp.kind == "declined"
    assert mp.parser_lane == MODAL_LANE_DECLINED


def test_bare_on_necessive_fires() -> None:
    mp = parse_modal_sentence("Hakemus on toimitettava määräajassa.")
    (core,) = mp.cores
    assert core.kind == KIND_OBLIGATION
    assert core.cue == "on"


# ---------------------------------------------------------------------------
# Multiple cores in one sentence
# ---------------------------------------------------------------------------


def test_multiple_cores_one_sentence() -> None:
    text = "Hakija saa hakea muutosta; viranomaisen on annettava päätös."
    mp = parse_modal_sentence(text)
    kinds = [c.kind for c in mp.cores]
    assert KIND_PERMISSION in kinds
    assert KIND_OBLIGATION in kinds
    assert_total_ownership(mp)


# ---------------------------------------------------------------------------
# Projection + projection key
# ---------------------------------------------------------------------------


def test_projection_key_form() -> None:
    assert modal_key("ei saa", POLARITY_NEGATIVE, VOICE_ACTIVE) == "ei saa:negative:active"
    mp = parse_modal_sentence("Viranomainen ei saa luovuttaa tietoja.")
    assert projection_modal_keys(mp) == {"ei saa:negative:active"}


def test_projection_excludes_kind() -> None:
    # The kind is NOT in the comparison key (production is surface-fact-only and
    # does not classify kind); the key is token:polarity:voice only.
    mp = parse_modal_sentence("Hakija saa hakea muutosta.")
    keys = projection_modal_keys(mp)
    assert keys == {"saa:affirmative:active"}
    assert "permission" not in next(iter(keys))


# ---------------------------------------------------------------------------
# Parity with the production oracle on bound-actor witnesses (census MATCH)
# ---------------------------------------------------------------------------


def test_census_match_on_bound_actor_witnesses() -> None:
    # When a registered actor binds the modal, projection and oracle agree → match.
    for text in (
        "Viranomaisen on tehtävä päätös viipymättä.",
        "Hakija saa hakea muutosta päätökseen.",
        "Viranomainen ei saa luovuttaa tietoja sivulliselle.",
        "Työnantaja on velvollinen järjestämään työterveyshuollon.",
    ):
        proj = projection_modal_keys(parse_modal_sentence(text))
        orc = _modal_oracle_keys_for_span(text)
        assert proj == orc, (text, proj, orc)
        assert classify(proj, orc, declined=False) == "match"


def test_census_superset_on_unbound_passive() -> None:
    # An impersonal passive provision verb with no registered actor yields NO
    # production frame (weak oracle) but a construction core → superset
    # (construction-recall-candidate, reported neutrally).
    text = "Asetuksella säädetään tarkemmin menettelystä."
    proj = projection_modal_keys(parse_modal_sentence(text))
    orc = _modal_oracle_keys_for_span(text)
    assert proj  # construction found a core
    assert not orc  # weak oracle found nothing
    assert classify(proj, orc, declined=False) == "superset"


# ---------------------------------------------------------------------------
# Census wiring on a real momentti via the segment selector
# ---------------------------------------------------------------------------


def test_segment_selector_yields_modal_units() -> None:
    body = (
        "Viranomaisen on tehtävä päätös viipymättä. "
        "Hakija saa hakea muutosta. "
        "Päätös on lainvoimainen."  # copula — out of family, not yielded
    )
    units = list(_modal_segment_selector("test/1", body))
    # The two deontic sentences are in scope; the copula sentence is not.
    markers = {u.declared_marker for u in units}
    assert any("obligation" in m for m in markers)
    assert any("permission" in m for m in markers)
    assert all(u.parser_lane == MODAL_LANE_CONSTRUCTION_OWNED for u in units)
    assert all(u.totality_ok for u in units)
