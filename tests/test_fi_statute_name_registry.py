"""Gate for the statute-NAME -> id registry (M2 / R5a substrate)."""

from __future__ import annotations

import datetime as dt

from lawvm.finland.references.registries import (
    StatuteNameEntry,
    build_registry,
)


def _fixture_registry():
    """A small hand registry, incl. one name with two temporal versions.

    ``Kuntalaki`` was enacted as 365/1995 and re-enacted (same name) as
    410/2015 --- a real both-acts-one-name situation.  The fixtures use
    open/closed windows so the temporal filter has something to bite on.
    """
    return build_registry(
        [
            # Single, unambiguous, current.
            ("1898/34-001", "Holhouslaki", dt.date(1898, 1, 1), None),
            ("1895/37-001", "Ulosottolaki", dt.date(1895, 1, 1), None),
            # A multi-word title (head inflects, modifier rides invariant).
            ("1962/282", "Vesiasetus", dt.date(1962, 1, 1), None),
            # ONE name, TWO acts over time: old (closed) + new (open).
            StatuteNameEntry(
                statute_id="1995/365",
                canonical_title="Kuntalaki",
                valid_from=dt.date(1995, 7, 1),
                valid_to=dt.date(2015, 5, 1),
            ),
            StatuteNameEntry(
                statute_id="2015/410",
                canonical_title="Kuntalaki",
                valid_from=dt.date(2015, 5, 1),
                valid_to=None,
            ),
        ],
    )


def test_known_single_name_resolves() -> None:
    reg = _fixture_registry()
    res = reg.lookup("Holhouslaki")
    assert res.status == "single"
    assert [c.statute_id for c in res.candidates] == ["1898/34-001"]


def test_inflected_genitive_resolves_via_generated_forms() -> None:
    """A genitive of the head (``Holhouslain``) must resolve --- the whole point.

    The nominative title is ``Holhouslaki``; the genitive ``Holhouslain`` is a
    generated head-inflection variant, never stored as a literal.
    """
    reg = _fixture_registry()
    res = reg.lookup("Holhouslain")
    assert res.status == "single"
    assert res.candidates[0].statute_id == "1898/34-001"

    # Inessive too (``Ulosottolaissa``), and on a multi-word title.
    assert reg.lookup("Ulosottolaissa").status == "single"
    assert reg.lookup("Vesiasetuksen").status == "single"
    assert reg.lookup("Vesiasetuksen").candidates[0].statute_id == "1962/282"


def test_two_version_name_without_as_of_is_multiple() -> None:
    """Fail-loud: a name covering two acts over time is ``multiple``, not newest."""
    reg = _fixture_registry()
    res = reg.lookup("Kuntalaki")
    assert res.status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1995/365", "2015/410"}

    # Same fail-loud behaviour on an inflected surface.
    assert reg.lookup("Kuntalain").status == "multiple"


def test_two_version_name_with_as_of_disambiguates() -> None:
    reg = _fixture_registry()
    old = reg.lookup("Kuntalaki", as_of=dt.date(2000, 1, 1))
    assert old.status == "single"
    assert old.candidates[0].statute_id == "1995/365"

    new = reg.lookup("Kuntalaki", as_of=dt.date(2020, 1, 1))
    assert new.status == "single"
    assert new.candidates[0].statute_id == "2015/410"

    # The boundary day belongs to the new act (valid_to is exclusive).
    assert (
        reg.lookup("Kuntalaki", as_of=dt.date(2015, 5, 1)).candidates[0].statute_id
        == "2015/410"
    )


def test_unknown_name_is_none() -> None:
    reg = _fixture_registry()
    assert reg.lookup("Tämmöistälakiaeiole").status == "none"
    assert reg.lookup("Holhouslaki").status == "single"  # sanity: registry works

    # A known name with no act in force at ``as_of`` is also ``none``.
    assert reg.lookup("Kuntalaki", as_of=dt.date(1990, 1, 1)).status == "none"


def test_repealed_version_dropped_by_as_of_citing() -> None:
    """A repealed-and-re-enacted name resolves to the in-force version after repeal.

    Mirrors the real ``esitutkintalaki`` collision: 449/1987 was repealed by
    805/2011 effective 2014-01-01 (the in-corpus oracle supersession date now
    populated as ``valid_to``). A RECENT body citing the name drops the repealed
    old version; an OLD-era body still resolves to the old one; the ENACTED-BUT-
    NOT-YET-REPEALED overlap (2011-2014, both validly in force) stays AMBIGUOUS.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                statute_id="1987/449",
                canonical_title="Esitutkintalaki",
                valid_from=dt.date(1987, 4, 30),
                valid_to=dt.date(2014, 1, 1),  # repealed by 805/2011, eff. 2014
            ),
            StatuteNameEntry(
                statute_id="2011/805",
                canonical_title="Esitutkintalaki",
                valid_from=dt.date(2011, 7, 22),
                valid_to=None,
            ),
        ],
    )
    # Whole-timeline lookup stays ambiguous (no silent newest pick).
    assert reg.lookup("Esitutkintalaki").status == "multiple"

    # Recent body: old version is past its repeal -> single, the in-force act.
    recent = reg.lookup("Esitutkintalaissa", as_of=dt.date(2019, 3, 15))
    assert recent.status == "single"
    assert recent.candidates[0].statute_id == "2011/805"

    # Old-era body (before the new act existed): the old version.
    old = reg.lookup("Esitutkintalaki", as_of=dt.date(1990, 1, 1))
    assert old.status == "single"
    assert old.candidates[0].statute_id == "1987/449"

    # Overlap (new enacted 2011, old not repealed until 2014): both validly in
    # force -> fail-loud AMBIGUOUS, never a silent pick.
    overlap = reg.lookup("Esitutkintalaki", as_of=dt.date(2012, 1, 1))
    assert overlap.status == "multiple"
    assert {c.statute_id for c in overlap.candidates} == {"1987/449", "2011/805"}


def test_build_registry_accepts_two_tuple() -> None:
    reg = build_registry([("1889/39-001", "Rikoslaki")])
    res = reg.lookup("Rikoslaki")
    assert res.status == "single"
    assert res.candidates[0].statute_id == "1889/39-001"
    # Untimed entry => no as_of filter ever excludes it.
    assert reg.lookup("Rikoslain", as_of=dt.date(2020, 1, 1)).status == "single"
