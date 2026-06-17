"""Tests for the reverse-morphology lemma index (M1 inversion).

The KEY invariant is round-trip soundness: every form M1 generates for a known
head must analyze back to (at least) that head.  Inversion and generation agree
because generation is deterministic and the lemma set is closed.
"""

from __future__ import annotations

import pytest

from lawvm.finland.morphology.api import MorphNumber
from lawvm.finland.morphology.generate import generate_forms
from lawvm.finland.morphology.heads import _HEADS, head_entry, is_known_head
from lawvm.finland.morphology.lemma_index import LemmaIndex, build_lemma_index


def test_roundtrip_soundness_over_full_known_head_inventory() -> None:
    """Every generated form of every known head analyzes back to that head.

    This is the central soundness invariant: generation and inversion agree.
    """
    idx = build_lemma_index()
    checked = 0
    for lemma in _HEADS:
        entry = head_entry(lemma)
        forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
        for form in forms:
            # M1 declines some (case, number) pairs (empty surface,
            # certainty="unsupported"); those are not real outputs to index.
            if form.certainty != "deterministic" or not form.surface:
                continue
            assert lemma in idx.analyze(form.surface), (
                f"{lemma} -> {form.surface!r} ({form.case.name}/{form.number.name}) "
                f"did not round-trip; got {idx.analyze(form.surface)}"
            )
            checked += 1
    # Sanity: we actually exercised a non-trivial number of forms.
    assert checked > 100


def test_default_index_covers_the_closed_head_inventory() -> None:
    idx = build_lemma_index()
    assert set(idx.lemmas) == set(_HEADS)
    for lemma in idx.lemmas:
        assert is_known_head(lemma)


@pytest.mark.parametrize(
    ("surface", "lemma"),
    [
        ("laissa", "laki"),
        ("lain", "laki"),
        ("asetuksen", "asetus"),
        ("pykälän", "pykälä"),
        ("momentissa", "momentti"),
    ],
)
def test_concrete_reversals(surface: str, lemma: str) -> None:
    """Hand-verified reversals against the actual M1 generator output."""
    idx = build_lemma_index()
    assert idx.analyze(surface) == (lemma,)
    assert idx.lemma_of(surface) == lemma


def test_nominative_lemmas_reverse_to_themselves() -> None:
    """The bare nominative is itself an indexed form -> reverses to its lemma."""
    idx = build_lemma_index()
    for lemma in ("laki", "asetus", "pykälä", "momentti"):
        assert lemma in idx.analyze(lemma)


def test_normalization_casefold_and_strip() -> None:
    idx = build_lemma_index()
    assert idx.analyze("  Laissa  ") == ("laki",)
    assert idx.analyze("ASETUKSEN") == ("asetus",)


def test_unknown_surface_returns_empty_not_a_guess() -> None:
    """A surface outside the closed vocabulary is an honest unknown."""
    idx = build_lemma_index()
    assert idx.analyze("koira") == ()
    assert idx.analyze("xyzzy") == ()
    assert idx.lemma_of("koira") is None


def test_no_genuine_collision_in_default_head_set() -> None:
    """The default known-head inventory has no shared inflected surface.

    (Documents the state of the inventory; the synthetic test below proves the
    fail-loud behavior that WOULD fire if a collision existed.)
    """
    idx = build_lemma_index()
    ambiguous = {s: idx.analyze(s) for s in idx._map if len(idx.analyze(s)) > 1}
    assert ambiguous == {}


def test_ambiguity_is_surfaced_fail_loud_synthetic() -> None:
    """Construct a 2-lemma collision and assert both are returned, no pick.

    Since no genuine collision exists in the head set, we build a synthetic
    :class:`LemmaIndex` whose map collides one surface onto two lemmas, and
    assert analyze() returns BOTH (sorted) and lemma_of() returns None.
    """
    idx = LemmaIndex(
        _map={
            "shared": ("alpha", "beta"),
            "alpha-only": ("alpha",),
        },
        lemmas=("alpha", "beta"),
    )
    assert idx.analyze("shared") == ("alpha", "beta")  # both, sorted
    assert idx.lemma_of("shared") is None  # never picks one
    assert idx.lemma_of("alpha-only") == "alpha"  # unambiguous -> single
    assert idx.lemma_of("missing") is None  # unknown -> None too


def test_caller_supplied_lemmas_restrict_the_vocabulary() -> None:
    """An explicit lemma set indexes only those lemmas (closed-set guarantee)."""
    idx = build_lemma_index(["laki", "asetus"])
    assert set(idx.lemmas) == {"laki", "asetus"}
    assert idx.analyze("laissa") == ("laki",)
    # pykälä is a known head but NOT in this caller-supplied set -> unknown.
    assert idx.analyze("pykälän") == ()


def test_unknown_lemma_fails_loud_with_keyerror() -> None:
    """Membership in the inflectable set is never guessed."""
    with pytest.raises(KeyError):
        build_lemma_index(["notahead"])


def test_determinism_repeated_builds_agree() -> None:
    a = build_lemma_index(["laki", "asetus", "pykälä"])
    b = build_lemma_index(["pykälä", "asetus", "laki"])  # different order in
    assert a.analyze("laissa") == b.analyze("laissa")
    assert dict(a._map) == dict(b._map)


def test_pronouns_are_out_of_scope_for_m1() -> None:
    """Document the wart-retirement limit: M1 has no pronoun paradigms.

    Pronouns (joka/mikä/se/tämä/ne/kaikki) are irregular and are NOT known
    heads, so M1 cannot generate ``jolla``/``sillä`` etc.  They are therefore
    out of scope for this index; the pronoun-adessive blocklist in
    defined_terms.py is NOT replaceable by this index as-is (would need M1 to
    gain a pronoun paradigm table first).
    """
    for pronoun in ("joka", "mikä", "se", "tämä", "ne", "kaikki"):
        assert not is_known_head(pronoun)
        with pytest.raises(KeyError):
            head_entry(pronoun)
