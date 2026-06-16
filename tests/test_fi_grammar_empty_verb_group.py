"""Differential tests for empty-verb-group recovery in the verb-group driver.

A multi-verb johtolause can name an un-modelled provision after a verb (e.g.
``soveltamissäännöksen N momentti`` / a free-text provision name) for which none
of the wired structural-target families recognizes a target. The old parser
yields an empty target list for that verb and DROPS the group, then continues to
the next verb. These tests assert the NEW parser reproduces that grouping
byte-identically — and that it still DECLINES (rather than silently dropping a
real group) when the un-modelled span carries a structural anchor the old parser
would have recognized.
"""

from __future__ import annotations

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar import parser as new_parser
from lawvm.finland.johtolause.grammar.diff import compare_surface_parsers
from lawvm.finland.johtolause.grammar.parser import OutOfScope


def _assert_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_empty_first_verb_group_is_dropped_and_next_groups_recovered() -> None:
    # The first verb (``muutetaan``) names an un-modelled provision with no
    # structural anchor; the old parser drops that group and keeps the later
    # ``kumotaan`` / ``lisätään`` groups. The new parser must match byte-for-byte.
    _assert_zero_delta(
        "muutetaan jokin nimetön säännös, kumotaan 5 § ja lisätään 6 §:ään uusi 2 momentti"
    )


def test_empty_first_group_before_conjoined_second_verb() -> None:
    # ``kumotaan <un-modelled provision> ja muutetaan N §`` — the kumotaan group
    # is empty (no anchor in its span) and dropped; muutetaan carries the target.
    _assert_zero_delta("kumotaan jokin nimetön säännös ja muutetaan 7 §")


def test_un_modelled_group_with_structural_anchor_declines() -> None:
    # The trailing ``muutetaan`` group names ``mainitun lain 6 §:n 1 momentti``
    # (a backref-led target the wired families cannot reproduce). Its span DOES
    # carry a structural anchor (``§`` / ``momentti``), so dropping it would lose
    # a verb group the old parser keeps — the driver must decline loudly.
    text = (
        "kumotaan oikeudenkäymiskaaren 22 luku ja muutetaan mainitun lain "
        "6 §:n 1 momentti näin kuuluvaksi:"
    )
    try:
        new_parser.parse(*_tokens_for(text))
    except OutOfScope as exc:
        assert "not a target at target position" in str(exc)
    else:  # pragma: no cover - guard against silent miscompile
        raise AssertionError("expected OutOfScope decline, parser accepted the clause")


def test_named_provision_first_target_group_is_dropped_whole() -> None:
    # The ``muutetaan`` group opens on an un-modellable named sub-provision
    # (``4 §:ään sisältyvä kolmas viranomaisten ryhmä``) — a ``§`` followed by a
    # bare descriptive WORD run the wired families (and the old ``_target``) cannot
    # anchor. The old ``_target_list`` returns ``[]`` and drops the WHOLE group
    # despite a later ``§`` in its span; the new parser must do the same and keep
    # the ``kumotaan`` group, byte-for-byte.
    _assert_zero_delta(
        "muutetaan 4 §:ään sisältyvä kolmas viranomaisten ryhmä, kumotaan 5 §"
    )


def test_named_provision_koskeva_nimike_group_is_dropped() -> None:
    # ``N §:n <name> koskeva nimike`` — another named-provision opener the old
    # parser drops whole; the following ``muutetaan`` group is kept.
    _assert_zero_delta(
        "kumotaan 14 §:n pelikortteja koskeva nimike, muutetaan 7 §"
    )


def test_section_anchor_without_descriptive_word_still_declines() -> None:
    # The trailing ``muutetaan`` group opens on ``mainitun lain 6 §:n 1 momentti``
    # — the ``§`` is followed by a STRUCTURAL sub-noun (``momentti``), not a bare
    # descriptive WORD, so it is NOT a dropped named provision. The old parser
    # keeps this group, so the driver must still decline loudly rather than drop.
    text = (
        "kumotaan oikeudenkäymiskaaren 22 luku ja muutetaan mainitun lain "
        "6 §:n 1 momentti näin kuuluvaksi:"
    )
    try:
        new_parser.parse(*_tokens_for(text))
    except OutOfScope as exc:
        assert "not a target at target position" in str(exc)
    else:  # pragma: no cover - guard against silent miscompile
        raise AssertionError("expected OutOfScope decline, parser accepted the clause")


def test_minka_ohella_continuation_is_recovered() -> None:
    # A ``minkä ohella <sec> … muutetaan`` connective continuation: the old
    # ``_target`` reaches past the two-WORD lead-in and keeps the following section
    # ref. The driver consumes ``minkä ohella`` and recognizes ``48 §`` natively,
    # reproducing the old grouping byte-for-byte (no decline).
    _assert_zero_delta("muutetaan 93 §, minkä ohella 48 §:n 1 momentti muutetaan")


def _tokens_for(text: str):
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    return tokens, (jolloin if jolloin else None)
