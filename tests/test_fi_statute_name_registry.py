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
    assert res.registry_status == "single"
    assert [c.statute_id for c in res.candidates] == ["1898/34-001"]


def test_inflected_genitive_resolves_via_generated_forms() -> None:
    """A genitive of the head (``Holhouslain``) must resolve --- the whole point.

    The nominative title is ``Holhouslaki``; the genitive ``Holhouslain`` is a
    generated head-inflection variant, never stored as a literal.
    """
    reg = _fixture_registry()
    res = reg.lookup("Holhouslain")
    assert res.registry_status == "single"
    assert res.candidates[0].statute_id == "1898/34-001"

    # Inessive too (``Ulosottolaissa``), and on a multi-word title.
    assert reg.lookup("Ulosottolaissa").registry_status == "single"
    assert reg.lookup("Vesiasetuksen").registry_status == "single"
    assert reg.lookup("Vesiasetuksen").candidates[0].statute_id == "1962/282"


def test_two_version_name_without_as_of_is_multiple() -> None:
    """Fail-loud: a name covering two acts over time is ``multiple``, not newest."""
    reg = _fixture_registry()
    res = reg.lookup("Kuntalaki")
    assert res.registry_status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1995/365", "2015/410"}

    # Same fail-loud behaviour on an inflected surface.
    assert reg.lookup("Kuntalain").registry_status == "multiple"


def test_two_version_name_with_as_of_disambiguates() -> None:
    reg = _fixture_registry()
    old = reg.lookup("Kuntalaki", as_of=dt.date(2000, 1, 1))
    assert old.registry_status == "single"
    assert old.candidates[0].statute_id == "1995/365"

    new = reg.lookup("Kuntalaki", as_of=dt.date(2020, 1, 1))
    assert new.registry_status == "single"
    assert new.candidates[0].statute_id == "2015/410"

    # The boundary day belongs to the new act (valid_to is exclusive).
    assert (
        reg.lookup("Kuntalaki", as_of=dt.date(2015, 5, 1)).candidates[0].statute_id
        == "2015/410"
    )


def test_unknown_name_is_none() -> None:
    reg = _fixture_registry()
    assert reg.lookup("Tämmöistälakiaeiole").registry_status == "none"
    assert reg.lookup("Holhouslaki").registry_status == "single"  # sanity: registry works

    # A known name with no act in force at ``as_of`` is also ``none``.
    assert reg.lookup("Kuntalaki", as_of=dt.date(1990, 1, 1)).registry_status == "none"


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
    assert reg.lookup("Esitutkintalaki").registry_status == "multiple"

    # Recent body: old version is past its repeal -> single, the in-force act.
    recent = reg.lookup("Esitutkintalaissa", as_of=dt.date(2019, 3, 15))
    assert recent.registry_status == "single"
    assert recent.candidates[0].statute_id == "2011/805"

    # Old-era body (before the new act existed): the old version.
    old = reg.lookup("Esitutkintalaki", as_of=dt.date(1990, 1, 1))
    assert old.registry_status == "single"
    assert old.candidates[0].statute_id == "1987/449"

    # Overlap (new enacted 2011, old not repealed until 2014): both validly in
    # force -> fail-loud AMBIGUOUS, never a silent pick.
    overlap = reg.lookup("Esitutkintalaki", as_of=dt.date(2012, 1, 1))
    assert overlap.registry_status == "multiple"
    assert {c.statute_id for c in overlap.candidates} == {"1987/449", "2011/805"}


def test_build_registry_accepts_two_tuple() -> None:
    reg = build_registry([("1889/39-001", "Rikoslaki")])
    res = reg.lookup("Rikoslaki")
    assert res.registry_status == "single"
    assert res.candidates[0].statute_id == "1889/39-001"
    # Untimed entry => no as_of filter ever excludes it.
    assert reg.lookup("Rikoslain", as_of=dt.date(2020, 1, 1)).registry_status == "single"


# ---------------------------------------------------------------------------
# Trailing-period title normalization (Palolaki. / Verotuslaki.)
# ---------------------------------------------------------------------------


def test_trailing_period_title_inflects_and_resolves() -> None:
    """A title stored with a trailing full stop still head-splits and inflects.

    ``Palolaki.`` (1960/465, the corpus stores the period) must resolve a
    period-free inflected citation ``palolain`` — the terminator is orthography,
    not a name token, so it is stripped before the head split + inflection.
    """
    reg = build_registry([StatuteNameEntry("1960/465", "Palolaki.")])
    # The nominative without the period resolves.
    assert reg.lookup("Palolaki").registry_status == "single"
    # An inflected (genitive) period-free citation resolves via generated forms.
    res = reg.lookup("palolain")
    assert res.registry_status == "single"
    assert res.candidates[0].statute_id == "1960/465"


def test_trailing_period_twin_acts_are_ambiguous_not_false_single() -> None:
    """Period normalization correctly surfaces a real both-acts-one-name pair.

    ``Huoneenvuokralaki.`` (1925/166) and ``Huoneenvuokralaki`` (1987/653) are two
    distinct acts of the same name. Without period stripping the 1925 title could
    not inflect, so a citation ``huoneenvuokralain`` resolved to the 1987 act
    alone (a false single that silently ignored the 1925 act). After stripping,
    both inflect and the bare citation is honestly AMBIGUOUS (no as_of filter).
    """
    reg = build_registry(
        [
            StatuteNameEntry("1925/166", "Huoneenvuokralaki."),
            StatuteNameEntry("1987/653", "Huoneenvuokralaki"),
        ]
    )
    res = reg.lookup("huoneenvuokralain")
    assert res.registry_status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1925/166", "1987/653"}


# ---------------------------------------------------------------------------
# Content-word-set fallback (inflection-robust descriptive-title matching)
# ---------------------------------------------------------------------------


def test_content_word_set_resolves_inflection_difference() -> None:
    """A descriptive cite differing only by premodifier inflection resolves.

    Official title ``Laki maatalousyrittäjien luopumiskorvauksesta`` (1992/1330,
    singular ``luopumiskorvauksesta``) cited as ``laki maatalousyrittäjien
    luopumiskorvauksista`` (plural). Exact-surface lookup misses; the content-word
    -set fallback collapses the sg/pl difference and resolves to the unique id.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                "1992/1330", "Laki maatalousyrittäjien luopumiskorvauksesta"
            )
        ]
    )
    # exact surface (sg) hits the normal index
    assert reg.lookup("laki maatalousyrittäjien luopumiskorvauksesta").registry_status == "single"
    # plural premodifier MISSES the exact index ...
    assert reg.lookup("laki maatalousyrittäjien luopumiskorvauksista").registry_status == "none"
    # ... but the content-word-set fallback resolves it to the unique id.
    res = reg.lookup_content_word_set("laki maatalousyrittäjien luopumiskorvauksista")
    assert res.registry_status == "single"
    assert res.candidates[0].statute_id == "1992/1330"


def test_content_word_set_head_must_match() -> None:
    """A ``laki`` cite never resolves to an ``asetus`` act of the same subject.

    A law and a decree on the same matter are different statutes; the head is part
    of the content key, so the fallback declines a cross-head match.
    """
    reg = build_registry(
        [StatuteNameEntry("1990/1177", "Asetus valtiontalouden tarkastuksesta")]
    )
    # The asetus act resolves under an asetus cite (head matches, sg/pl collapse).
    assert (
        reg.lookup_content_word_set("asetus valtiontalouden tarkastuksista").registry_status
        == "single"
    )
    # A laki cite of the same subject does NOT resolve to the asetus act.
    assert (
        reg.lookup_content_word_set("laki valtiontalouden tarkastuksista").registry_status
        == "none"
    )


def test_content_word_set_multiple_is_ambiguous_never_picked() -> None:
    """Two acts sharing the same content-word set -> ambiguous, never picked."""
    reg = build_registry(
        [
            StatuteNameEntry("1990/100", "Laki valtiontalouden tarkastuksesta"),
            StatuteNameEntry("1993/267", "Laki valtiontalouden tarkastuksessa"),
        ]
    )
    res = reg.lookup_content_word_set("laki valtiontalouden tarkastuksista")
    assert res.registry_status == "multiple"
    assert {c.statute_id for c in res.candidates} == {"1990/100", "1993/267"}


def test_content_word_set_garbage_complement_declined() -> None:
    """A garbage / clause-leak complement is refused (never coincidentally hits).

    A complement carrying an extraction-noise token (``kun``, ``mitä``, ``osin``)
    is not a clean title body — no content set is built, so it cannot match.
    """
    reg = build_registry(
        [StatuteNameEntry("2018/1", "Laki finanssivalvonnan järjestämisestä")]
    )
    assert reg.lookup_content_word_set("laki kun finanssivalvonnasta").registry_status == "none"
    assert (
        reg.lookup_content_word_set("laki mitä finanssivalvonnasta").registry_status == "none"
    )


def test_content_word_set_single_stem_too_generic_declined() -> None:
    """A single-content-word complement is too generic for the fallback.

    The exact-key lane already resolves clean single-noun titles; the content-word
    fallback requires >=2 distinctive stems so a 1-stem set cannot cause an
    over-broad match.
    """
    reg = build_registry([StatuteNameEntry("2000/1", "Laki edistämisestä")])
    assert reg.lookup_content_word_set("laki edistämisestä").registry_status == "none"


def test_content_word_set_amendment_title_not_indexed() -> None:
    """An amendment title is never a content-word target (cite names the base act).

    ``Laki X annetun lain muuttamisesta`` is an amending act; the descriptive cite
    ``X annetun lain`` denotes the BASE act, so amendment titles must not appear in
    the content index.
    """
    reg = build_registry(
        [
            StatuteNameEntry(
                "1987/667", "Laki virvoitusjuomaverosta annetun lain muuttamisesta"
            )
        ]
    )
    # Even the amendment's own exact body should not be a content target.
    assert reg.lookup_content_word_set("laki virvoitusjuomaverosta").registry_status == "none"


def test_content_word_set_folded_matches_sg_pl_stem_artifact() -> None:
    """The folded lane matches a sg-cite onto a pl-official-title base act.

    ``Laki viranomaisten toiminnan julkisuudesta`` (pl) is indexed; a singular cite
    ``laki viranomaisen toiminnan julkisuudesta`` misses the plain whole-set (the
    open-analyzer stems ``viranomaise``/``viranomais`` differ) but the trailing-
    vowel-folded lane collapses that artifact to a single hit.
    """
    reg = build_registry(
        [StatuteNameEntry("1999/621", "Laki viranomaisten toiminnan julkisuudesta")]
    )
    # plain whole-set MISSES the singular cite
    assert (
        reg.lookup_content_word_set(
            "laki viranomaisen toiminnan julkisuudesta"
        ).registry_status
        == "none"
    )
    # folded lane HITS it uniquely
    folded = reg.lookup_content_word_set_folded(
        "laki viranomaisen toiminnan julkisuudesta"
    )
    assert folded.registry_status == "single"
    assert folded.candidates[0].statute_id == "1999/621"


def test_content_word_set_folded_non_descriptive_cite_is_none() -> None:
    """A non-head-first (compound-nickname) cite has no folded content key."""
    reg = build_registry([StatuteNameEntry("1999/621", "Laki viranomaisten toiminnan julkisuudesta")])
    assert reg.lookup_content_word_set_folded("verotuslaki").registry_status == "none"
