"""Negative (non-statute) collision paradigms for the by-name head gate.

The by-name recognizer (:mod:`lawvm.finland.references.by_name`) triggers on any
token ending in an oblique *statute-head* surface (``lain`` / ``laissa`` /
``laista`` ...).  A small number of ordinary Finnish paradigms inflect to a
surface whose **tail is byte-identical** to a ``laki`` oblique even though the
word is not a statute at all:

    ``veronalaista``  -- ``-lainen`` adjective partitive (``veron-alainen``)
    ``oppilaille``    -- ``-las`` agent-noun plural allative (``oppilas``)
    ``jollain``       -- ``jokin`` pronoun reduced oblique (``joll-`` stem)
    ``tämänlain``     -- determiner glued to ``laki`` (elided space, OCR source)

Historically each family was caught by a hand-written suffix regex in
``by_name.py``.  Suffix-substring matching has a consonant-gradation bug class
(``'asetus' not in 'asetuksen'``) and is opaque to the morphology engine.  This
module folds the four ad-hoc tables into entries in the SAME generation engine
(M1) that produces the positive statute heads, as **distinct non-statute
lemmas**, so the by-name gate can reject them by *paradigm inversion* rather than
substring matching.

Design (mirrors the positive head model):

* The PRODUCTIVE families are paradigm *heads* (``alainen``, ``oppilas`` ...),
  not whole compounds.  Just as the positive head engine generates
  ``laki -> laissa`` and the regex strips the free modifier (``luonnonsuojelu``),
  the negative engine generates ``alainen -> alaista`` / ``oppilas -> oppilaille``
  and the gate strips the free modifier (``veron`` / ``rintama``).  Modifier
  invariance is the SAME property the positive heads rely on.

* Where M1 has a categorical rule (the ``-nen`` adjective class) the form is
  GENERATED, not stored (``alainen -> alaista``).  Where the paradigm is
  irregular or outside M1's modelled classes -- the closed demonstrative
  ``-lainen`` adjectives' irregular back-harmony (``tällaista``), the ``jokin``
  reduced pronoun obliques, the ``-As : -AA-`` agent-noun plural -- the exact
  closed surface set is enumerated directly.  Still closed and fail-loud, never
  a guessed open-world analysis.

* The determiner+``laki`` collapse is not a single paradigm but two glued words;
  it is detected from the PEELED MODIFIER: a complete closed-determiner
  inflection (``tämän`` / ``tässä`` / ``mainitun``) is never a statute modifier.

Soundness / boundary:
    * A token is rejected ONLY when a negative paradigm surface is STRICTLY
      LONGER than the bare ``laki`` oblique it shadows (its derivational /
      agentive stem extends past the bare head -- the morphological proof that
      the word is the negative paradigm, not ``modifier`` + bare head).  A plain
      ``Xlaista`` that is just ``X`` + the laki elative (``verolaista``) is NOT
      shadowed and survives as a real reference.
    * An unknown token (no negative paradigm matches) returns ``unknown`` --
      honest, never a silent guess.
    * The negative paradigms are a CLOSED set, verified against the full
      statute-name registry to share no resolvable real-act key (see the
      by-name + lemma-gate tests).  They can never reject a genuine ``-laki``
      compound.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .api import MorphCase, MorphEntry, MorphNumber
from .generate import generate_forms

# --------------------------------------------------------------------------- #
# The closed laki obliques that the by-name regex uses as triggers.  A negative
# paradigm only needs to *compete* on a token whose tail equals one of these.
# Listed for documentation / the gate's "at least as long" comparison.
# --------------------------------------------------------------------------- #
_LAKI_OBLIQUES: frozenset[str] = frozenset(
    {"lain", "laissa", "laista", "laiksi", "lailla", "laille", "lailta"}
)


# --------------------------------------------------------------------------- #
# Family 1: -lainen / -nainen demonstrative-adjective family (Kotus 38, -nen).
# M1 GENERATES this paradigm (the -nen morph_class).  Only the SG partitive
# (-laista / -naista) collides with the laki elative (laista); every other case
# inserts -se- and never collides.
#
# Two sub-cases, both sound under the strictly-longer-than-the-bare-oblique rule
# (see _shadowed_laki_oblique): the colliding partitive must extend the bare
# ``laista`` with a real derivational stem, so a plain ``Xlaista`` that is just
# ``X`` + laki elative (``verolaista``) is NEVER shadowed.
#   * ``alainen``  -- the PRODUCTIVE deverbal/relational head (veron-alainen,
#     työn-alainen, valvonnan-alainen): a free modifier rides invariant in
#     front, exactly like a statute modifier rides in front of laki.  Its
#     partitive ``alaista`` (7) strictly exceeds ``laista`` (6).
#   * the productive ``-lainen``/``-nainen`` derivational partitive over an OPEN
#     modifier (sellaista, samanlaista, toisenlaista, muunlaista ...): the
#     derivational linking segment before ``-laista`` (a doubled ``-l-``, a
#     genitive ``-n-``, an ``-ri-``/``-ka-``) is registered as a collision
#     SUFFIX (not a whole lemma), every variant strictly longer than the bare
#     oblique.  This is the morphological generalization of the historical hand
#     regex's sound classes.
# --------------------------------------------------------------------------- #
# The PRODUCTIVE relational head, generated by M1 (correct harmony: alaista).
# A free modifier rides invariant in front (veron-, työn-, valvonnan-), so the
# gate strips it exactly as it strips a statute modifier off laki.
_ADJ_NEN_PRODUCTIVE_HEAD = "alainen"

# The PRODUCTIVE -lainen/-nainen derivational partitive collision suffixes.  The
# -lainen/-nainen adjective is formed productively from a (usually genitive)
# modifier + the derivational -lainen suffix; its partitive is -laista.  What
# distinguishes the adjective partitive from a bare laki elative (modifier +
# laista) is the DERIVATIONAL LINKING SEGMENT immediately before -laista:
#   * a doubled -l- (vowel + ``llaista``): se-llaista, tä-llaista, tuo-llaista;
#   * an -n- (genitive modifier, ``nlaista``): muu-n-laista, toise-n-laista,
#     uude-n-laista, sama-n-laista, seuraava-n-laista, tarkoitetu-n-laista …;
#   * an -r-/-ri- (``rilaista``): e-ri-laista, monenkir-…;
#   * a -ka-/-kä- (``kaltaista``/``kalaista``): se-n-kaltaista, tuo-n-kaltaista.
# These linking segments NEVER occur before a real -laki elative (a statute
# modifier is a noun stem, not a genitive determiner / doubled-l adjective stem),
# and every such suffix is strictly longer than the bare ``laista`` oblique, so
# the strictly-longer rule keeps a plain ``Xlaista`` laki elative out.  Expressed
# as collision suffixes (not whole lemmas) because the modifier is OPEN: this is
# the morphological generalization of the historical hand regex's sound classes.
_PRODUCTIVE_ADJ_COLLISION_SUFFIXES: tuple[str, ...] = (
    "allaista",
    "ellaista",
    "illaista",
    "ollaista",
    "ullaista",
    "yllaista",
    "ällaista",
    "öllaista",
    "nlaista",
    "rilaista",
    "kalaista",
    "kälaista",  # minkälaista (front-harmony interrogative -lainen)
    "kaltaista",
    "nkaltaista",
)


# --------------------------------------------------------------------------- #
# Family 2: -las / -läs agent nouns (Kotus 41, -As : -AA-).  M1 has no rule for
# this class, so the colliding PLURAL obliques are supplied as an explicit
# closed paradigm.  All plural obliques are built on the -lai- stem and are
# byte-identical-tailed to a laki oblique.  Closed legal/administrative class:
# oppilas (pupil), sotilas (soldier), kokelas (cadet).  A free compound prefix
# (rintama-, vara-) rides invariant in front (rintamasotilaille).
# --------------------------------------------------------------------------- #
_AGENT_NOUN_LEMMAS: tuple[str, ...] = ("oppilas", "sotilas", "kokelas")
# Plural oblique case -> surface suffix on the -lai- stem (shared across the
# three lemmas; the stem before -lai- is the lemma minus its final -las/-läs).
_AGENT_NOUN_PL_OBLIQUES: dict[MorphCase, str] = {
    MorphCase.GEN: "lain",  # archaic/poetic pl gen (oppilain)
    MorphCase.INE: "laissa",
    MorphCase.ELA: "laista",
    MorphCase.ADE: "lailla",
    MorphCase.ABL: "lailta",
    MorphCase.ALL: "laille",
    MorphCase.TRA: "laiksi",
}


# --------------------------------------------------------------------------- #
# Family 3: jokin pronoun reduced obliques (joll- / joill- stems).  Irregular
# pronoun paradigm outside M1's modelled noun classes; the closed reduced
# oblique surface set is supplied explicitly.  Never compounds.
# --------------------------------------------------------------------------- #
_JOKIN_OBLIQUE_SURFACES: frozenset[str] = frozenset(
    {
        # singular joll-
        "jollain",
        "jollaiksi",
        "jollailla",
        "jollaille",
        "jollailta",
        "jollaissa",
        "jollaista",
        # plural joill-
        "joillain",
        "joillaiksi",
        "joillailla",
        "joillaille",
        "joillailta",
        "joillaissa",
        "joillaista",
    }
)


# --------------------------------------------------------------------------- #
# Family 4: closed determiners glued to a laki oblique (elided space / OCR).
# The "modifier" the by-name regex peels off is itself a COMPLETE inflection of
# a closed determiner lemma -- a real statute modifier is a noun stem, never a
# fully inflected determiner.  We generate the determiner inflections that
# appear before a glued laki oblique and check the peeled modifier against them.
# --------------------------------------------------------------------------- #
# determiner lemma -> the inflected surfaces that appear glued before laki.
# (Verified against the full statute-name registry: no real act modifier equals
# any of these -- see test_determiner_laki_collapse_is_not_a_statute_name.)
_DETERMINER_MODIFIER_SURFACES: frozenset[str] = frozenset(
    {
        "tämän",
        "tässä",
        "tästä",
        "tuon",
        "sen",
        "näiden",
        "niiden",
        "mainitun",
        "sellaisen",
        "kunkin",
        "saman",
        "kyseisen",
        "kyseisessä",
        "erään",
    }
)


def _shadowed_laki_oblique(surface: str) -> str | None:
    """Return the LONGEST laki oblique that ``surface`` ends in, else None.

    A negative surface only competes with the by-name trigger when it ends in a
    laki oblique (that is why the by-name regex fired).  The shadowed oblique's
    length is the discriminator: a negative paradigm only out-explains the
    statute reading when its colliding form is STRICTLY LONGER than the bare
    laki oblique (its derivational/agentive stem extends past the bare head),
    so ``verolaista`` (= ``vero`` + laki elative ``laista``, no extra stem) is
    NOT shadowed by the ``-lainen`` partitive, while ``sellaista`` /
    ``veronalaista`` (extra ``-l-``/``-al-`` derivational stem) is.
    """
    best: str | None = None
    for obl in _LAKI_OBLIQUES:
        if surface.endswith(obl) and (best is None or len(obl) > len(best)):
            best = obl
    return best


@dataclass(frozen=True, slots=True)
class _NegEntry:
    """A negative-paradigm surface and the lemma that generates it.

    ``shadows`` is the bare laki oblique the surface ends in; the gate rejects a
    token only when the negative surface is STRICTLY LONGER than ``shadows``
    (its non-head stem extends past the bare head -- a sound morphological
    proof, not a substring coincidence).
    """

    surface: str
    lemma: str
    shadows: str


def _neg_entry(surface: str, lemma: str) -> _NegEntry | None:
    """Build a _NegEntry iff ``surface`` ends in a laki oblique (else None)."""
    shadow = _shadowed_laki_oblique(surface)
    if shadow is None:
        return None
    return _NegEntry(surface=surface, lemma=lemma, shadows=shadow)


def _agent_noun_forms() -> list[_NegEntry]:
    """Enumerate the colliding plural-oblique surfaces of the agent nouns."""
    out: list[_NegEntry] = []
    for lemma in _AGENT_NOUN_LEMMAS:
        stem = lemma[:-3]  # drop -las / -läs -> oppi / soti / koke
        for suffix in _AGENT_NOUN_PL_OBLIQUES.values():
            entry = _neg_entry(stem + suffix, lemma)
            if entry is not None:
                out.append(entry)
    return out


def _adj_nen_forms() -> list[_NegEntry]:
    """Colliding -lainen/-nainen partitive surfaces.

    The PRODUCTIVE ``alainen`` head is GENERATED via the M1 ``-nen`` rule (full
    paradigm; only the partitive ``alaista`` collides, proven by keeping the
    forms whose tail is a laki oblique).  The other productive ``-lainen``/
    ``-nainen`` partitives have an OPEN modifier, so the derivational linking
    suffixes (``nlaista``, ``[vowel]llaista`` …) are registered as collision
    suffixes -- the morphological generalization of the historical hand regex's
    sound classes.  The strictly-longer-than-the-bare-oblique rule (in
    :meth:`NegativeParadigms.longest_suffix_match`) keeps a plain ``Xlaista``
    laki elative (``verolaista``) out.
    """
    out: list[_NegEntry] = []
    entry = MorphEntry(
        lemma_id=f"neg:{_ADJ_NEN_PRODUCTIVE_HEAD}",
        lemma=_ADJ_NEN_PRODUCTIVE_HEAD,
        referent_kind="adjective",
        morph_class="-nen",
    )
    for form in generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL)):
        if form.certainty != "deterministic" or not form.surface:
            continue
        neg = _neg_entry(form.surface.lower(), _ADJ_NEN_PRODUCTIVE_HEAD)
        if neg is not None:
            out.append(neg)
    for suffix in _PRODUCTIVE_ADJ_COLLISION_SUFFIXES:
        neg = _neg_entry(suffix, "-lainen")
        if neg is not None:
            out.append(neg)
    return out


@dataclass(frozen=True, slots=True)
class NegativeParadigms:
    """Closed index of non-statute collision surfaces, suffix-searchable.

    * ``whole_surfaces`` -- surfaces that are a COMPLETE non-statute word
      (``jollain``, ``alaista``): a token matches if it ENDS in one of these and
      the residual prefix is a plausible compound modifier.
    * ``determiner_modifiers`` -- complete determiner inflections that, when they
      are the peeled modifier before a laki oblique, mark a determiner-collapse.
    """

    whole_surfaces: tuple[_NegEntry, ...]
    determiner_modifiers: frozenset[str]
    _by_len: tuple[_NegEntry, ...]

    def longest_suffix_match(self, token: str) -> _NegEntry | None:
        """Return the longest negative surface that out-explains ``token``.

        A negative surface competes only when it is a suffix of ``token`` AND is
        STRICTLY LONGER than the bare laki oblique it shadows: the extra
        characters are the negative paradigm's own derivational/agentive stem
        (``-al-`` in ``alaista``, ``oppi-`` ... no -- the agentive stem rides in
        front, so its colliding surface ``oppilaille`` is whole-word longer than
        ``laille``).  This is the morphological proof that the token is the
        negative word, not ``modifier`` + bare laki oblique:

        * ``veronalaista`` -> ``alaista`` (7) > ``laista`` (6)  REJECT
        * ``sellaista``    -> ``sellaista`` (9) > ``laista`` (6) REJECT
        * ``verolaista``   -> only ``laista`` (6) is a suffix, NOT > 6  -> no
          match -> the bare laki elative survives as a real reference.
        * ``oppilaille``   -> ``oppilaille`` (10) > ``laille`` (6)  REJECT

        Longest-first so the most specific paradigm wins.
        """
        low = token.lower()
        for entry in self._by_len:
            if low.endswith(entry.surface) and len(entry.surface) > len(entry.shadows):
                return entry
        return None

    def is_determiner_modifier(self, modifier: str) -> bool:
        """True when ``modifier`` is a complete closed-determiner inflection."""
        return modifier.lower() in self.determiner_modifiers


@lru_cache(maxsize=None)
def negative_paradigms() -> NegativeParadigms:
    """Build the closed negative-paradigm index (memoized singleton)."""
    surfaces: list[_NegEntry] = []
    surfaces.extend(_adj_nen_forms())
    surfaces.extend(_agent_noun_forms())
    for s in _JOKIN_OBLIQUE_SURFACES:
        entry = _neg_entry(s, "jokin")
        if entry is not None:
            surfaces.append(entry)

    # Every entry already collides with a laki oblique by construction
    # (_neg_entry returns None otherwise).  Sort longest-first for the
    # most-specific-paradigm-wins longest-suffix match.
    by_len = tuple(sorted(set(surfaces), key=lambda e: len(e.surface), reverse=True))
    return NegativeParadigms(
        whole_surfaces=tuple(sorted(set(surfaces), key=lambda e: e.surface)),
        determiner_modifiers=_DETERMINER_MODIFIER_SURFACES,
        _by_len=by_len,
    )


__all__ = ["NegativeParadigms", "negative_paradigms"]
