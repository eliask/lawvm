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


def _tokens_for(text: str):
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    return tokens, (jolloin if jolloin else None)
