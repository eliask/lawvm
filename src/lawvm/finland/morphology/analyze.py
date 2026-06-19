"""OPEN-vocabulary deterministic morphological ANALYZER (M1 inverted).

:mod:`lemma_index` inverts M1 only for a CLOSED known-head set: it enumerates
each known lemma's whole paradigm and reverse-maps the surfaces.  On real legal
prose that covers a small fraction of tokens, because most tokens inflect a
lemma that is not in the closed head table.

This module generalizes the inversion to OPEN vocabulary WITHOUT abandoning the
LawVM discipline (deterministic, no statistical model, generation-first,
fail-loud).  The trick is that M1 generation is the ground truth: we never need
to *trust* a hypothesized analysis, we only need to *propose* candidates and
then VERIFY each one by forward round-trip.

THE ROUND-TRIP SAFETY GATE (what makes this provably sound):
    An analysis ``(lemma, morph_class, case, number)`` (plus the lexical
    ``gradation`` / ``single_k`` flags) is VALID iff ``generate_forms`` of the
    reconstructed :class:`MorphEntry` produces the EXACT input surface for that
    ``(case, number)``.  So the analyzer is:

        hypothesize plausible (lemma, class, flags) candidates
            -> for each, generate the full paradigm
            -> keep every (case, number) whose surface == input
            -> return the deduplicated set.

    Because the keep-filter is a real M1 output check, the analyzer can NEVER
    return an analysis the generator would not produce.  Liberal hypothesis
    generation is therefore safe: false hypotheses are filtered, not emitted.

HYPOTHESIS GENERATION (recall side --- liberal, the gate keeps it sound):
    1. CASE/NUMBER SUFFIX STRIPPING over the closed Finnish nominal suffix
       inventory the ``reference_v1`` profile generates (derived from
       :mod:`paradigm` / :mod:`plurals`, not invented).  Vowel harmony pairs the
       back/front variants.  Stripping a suffix exposes a candidate *stem*.
    2. STEM -> LEMMA INVERSION: each M1 stem-class builder maps lemma -> stem by
       a small rule; we invert each rule to recover candidate lemma(s) from the
       exposed stem (``asetukse-`` -> ``asetus``; ``lai-`` -> ``laki``;
       ``kaare-`` -> ``kaari``).  Where a forward rule is many-to-one we emit ALL
       pre-images.  The bare surface is also always tried as a nominative lemma.
    3. FLAG ENUMERATION: ``gradation`` and ``single_k`` are lexical (not
       recoverable from the surface), so we enumerate the small plausible flag
       space and let the round-trip gate select the combination(s) that work.

SCOPE: NOMINALS in the ``reference_v1`` case set (the M1 nominal classes).
Verbs are out of scope.  An out-of-vocabulary or un-invertible surface returns
the EMPTY tuple --- an honest unknown, never a fabricated lemma.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from .api import (
    MorphCase,
    MorphEntry,
    MorphNumber,
)
from .generate import generate_forms
from .stems import _CLASS_BUILDERS

_VOWELS = frozenset("aeiouyäö")

# The morph_class keys M1 knows how to build (single source of truth: the stem
# builder registry).  Iterating this keeps the analyzer in lock-step with the
# generator --- a new class added to M1 is automatically a hypothesis target.
_MORPH_CLASSES: tuple[str, ...] = tuple(_CLASS_BUILDERS)

# The lexical flag space.  ``gradation`` toggles the weak-grade alternation;
# ``single_k`` is the lexical k -> (zero|v|j) realization (only relevant when the
# stem actually carries a single k, but enumerating it is harmless: the gate
# discards combinations that do not reproduce the surface).
_GRADATION_OPTIONS: tuple[bool, ...] = (False, True)
_SINGLE_K_OPTIONS: tuple[str | None, ...] = (None, "zero", "v", "j")


@dataclass(frozen=True, slots=True)
class MorphAnalysis:
    """One verified open-vocabulary analysis of a surface form.

    The identity of an analysis is ``(lemma, morph_class, case, number)`` --- the
    information a consumer needs.  The lexical ``gradation`` / ``single_k`` flags
    are a verification-internal recovery detail (several flag combinations can
    yield the same surface for the same paradigm slot), so they are deliberately
    NOT part of the analysis: keeping them would split one linguistic analysis
    into many spurious duplicates.  ``MorphAnalysis`` is hashable + sorted so the
    analyzer returns a stable, deduplicated set.

    Every analysis is recovered by hypothesis + round-trip verification: there
    EXISTS a ``MorphEntry(lemma, morph_class, ...)`` whose ``generate_forms`` for
    ``(case, number)`` reproduces the analyzed surface exactly.
    """

    lemma: str
    morph_class: str
    case: MorphCase
    number: MorphNumber

    def _sort_key(self) -> tuple[str, str, str, str]:
        return (self.lemma, self.morph_class, self.case.name, self.number.name)


# --------------------------------------------------------------------------- #
# Suffix inventory (derived from paradigm.py / plurals.py).  Each entry strips a
# known surface suffix to expose the stem the suffix attached to.  These are
# hypothesis generators only --- the round-trip gate is the authority.
# --------------------------------------------------------------------------- #

# Closed-syllable singular suffixes that attach to the OBLIQUE (weak) stem.
# (rule_id ending -> the literal back/front-realized suffix strings)
_OBLIQUE_SUFFIXES: tuple[str, ...] = (
    "ssa", "ssä",   # INE -ssA
    "sta", "stä",   # ELA -stA
    "lla", "llä",   # ADE -llA
    "lta", "ltä",   # ABL -ltA
    "lle",          # ALL -lle (invariant e)
    "ksi",          # TRA -ksi
    "n",            # GEN -n   (kept last: shortest, broadest)
)

# Plural closed-syllable suffixes (the -i-/-j- marker is part of the stem, so we
# strip the whole tail and recover the singular stem during lemma inversion).
_PLURAL_SUFFIXES: tuple[str, ...] = (
    "issa", "issä",   # PL INE -issA
    "ista", "istä",   # PL ELA -istA
    "jen", "ien", "den", "ten",   # PL GEN
    "ja", "jä", "ia", "iä", "ita", "itä", "tta", "ttä",  # PL PART
)


def analyze_open(surface: str) -> tuple[MorphAnalysis, ...]:
    """Return every M1-round-trip-verified analysis of ``surface`` (open vocab).

    Hypothesizes candidate ``(lemma, morph_class, gradation, single_k)`` tuples
    from suffix stripping + stem inversion, then keeps only the ``(case,
    number)`` analyses whose forward generation reproduces ``surface`` exactly.

    Ambiguity is SURFACED, never ranked: when several analyses round-trip (e.g.
    a surface that is both a genitive of lemma X and an inessive of lemma Y, or a
    lemma that round-trips under two morph_classes), ALL are returned, sorted.
    An out-of-vocabulary / un-invertible surface returns the EMPTY tuple.
    """
    norm = surface.strip().casefold()
    if not norm:
        return ()

    candidates = _hypothesize_lemmas(norm)
    verified: set[MorphAnalysis] = set()
    for lemma, morph_class, gradation, single_k in candidates:
        verified.update(
            _verify(norm, lemma, morph_class, gradation, single_k),
        )
    return tuple(sorted(verified, key=MorphAnalysis._sort_key))


def _verify(
    norm: str,
    lemma: str,
    morph_class: str,
    gradation: bool,
    single_k: str | None,
) -> set[MorphAnalysis]:
    """Generate the full paradigm of one hypothesis; keep forms equal to ``norm``.

    This IS the safety gate: only ``(case, number)`` pairs whose generated
    surface byte-equals the input are accepted, so a returned analysis is a proof
    that M1 inflects ``lemma`` (under these flags) to ``norm``.
    """
    entry = MorphEntry(
        lemma_id=f"open:{lemma}",
        lemma=lemma,
        referent_kind="common",
        morph_class=morph_class,
        gradation=gradation,
        single_k=single_k,
    )
    try:
        forms = generate_forms(entry, numbers=(MorphNumber.SG, MorphNumber.PL))
    except (ValueError, KeyError, IndexError):
        # An un-buildable hypothesis (e.g. a too-short stem for the class) is not
        # an analysis --- fail loud locally by discarding it, never fabricate.
        return set()
    out: set[MorphAnalysis] = set()
    for form in forms:
        if form.certainty != "deterministic" or not form.surface:
            continue
        if form.surface.casefold() == norm:
            out.add(
                MorphAnalysis(
                    lemma=lemma,
                    morph_class=morph_class,
                    case=form.case,
                    number=form.number,
                ),
            )
    return out


def _hypothesize_lemmas(
    norm: str,
) -> set[tuple[str, str, bool, str | None]]:
    """Propose ``(lemma, morph_class, gradation, single_k)`` tuples for ``norm``.

    Liberal by design --- the round-trip gate in :func:`_verify` filters.  Three
    hypothesis sources: (1) the bare surface as a nominative lemma; (2) singular
    oblique-suffix stripping -> stem -> candidate lemmas; (3) plural-suffix
    stripping -> stem -> candidate lemmas.  Each candidate stem is expanded into
    candidate lemmas by inverting the M1 stem-class builders, then crossed with
    the lexical flag space.
    """
    lemma_class: set[tuple[str, str]] = set()

    # (1) The surface might itself be a nominative lemma (NOM is generated bare).
    # NOM output is morph_class-independent, so proposing the bare surface under
    # EVERY class would yield one spurious NOM duplicate per class; instead
    # propose it only under classes whose nominative-ending SHAPE is structurally
    # consistent with the surface (this is hypothesis pruning by the same ending
    # constraints the class builders require, NOT ranking --- equally sound).
    lemma_class.update((norm, c) for c in _nominative_classes(norm))

    # (2) Singular oblique suffixes (GEN/INE/ELA/ADE/ABL/ALL/TRA).  The exposed
    # "stem" can be a SINGULAR oblique stem OR (when it ends in -i-) a plural
    # ``weak_i_stem`` (plural INE/ELA are -ssA/-stA on the -i- stem); both lemma
    # reconstructions are tried, the gate selects.
    for suffix in _OBLIQUE_SUFFIXES:
        if norm.endswith(suffix) and len(norm) > len(suffix):
            stem = norm[: -len(suffix)]
            lemma_class.update(_singular_stem_lemmas(stem))
            lemma_class.update(_plural_stem_lemmas(stem))

    # The illative (-Vn / -seen) and partitive attach to the vowel / partitive
    # stem rather than a clean suffix; recover their stems explicitly.
    lemma_class.update(_illative_stem_lemmas(norm))
    lemma_class.update(_partitive_stem_lemmas(norm))

    # (3) Plural genitive / partitive suffixes (-jen/-ien/-den/-ten, -jA/-iA/-itA
    # /-ttA): strip, then invert the plural -i-/-j- marker back to a singular
    # stem before lemma inversion.
    for suffix in _PLURAL_SUFFIXES:
        if norm.endswith(suffix) and len(norm) > len(suffix):
            tail = norm[: -len(suffix)]
            for sg_stem in _plural_tail_to_singular_stems(tail):
                lemma_class.update(_singular_stem_lemmas(sg_stem))
    # Plural nominative -t attaches to the oblique (weak) stem + t.
    if norm.endswith("t") and len(norm) > 1:
        lemma_class.update(_singular_stem_lemmas(norm[:-1]))

    # Cross every (lemma, class) with the lexical flag space.
    out: set[tuple[str, str, bool, str | None]] = set()
    for (lemma, morph_class), gradation, single_k in product(
        lemma_class, _GRADATION_OPTIONS, _SINGLE_K_OPTIONS,
    ):
        out.add((lemma, morph_class, gradation, single_k))
    return out


# Inverse of gradation._CLUSTER_RULES (strong -> weak), used to recover the
# strong-grade nominative from a weak-grade oblique stem.  Mirrors the forward
# table; kept local so the analyzer reads as a self-contained inversion and the
# forward table stays the single forward authority.  ``t->d``/``p->v`` invert
# back to single ``t``/``p``; the geminates (kk/pp/tt) and assimilations
# (nn/ll/rr/mm/ng) invert to their strong cluster.  Over-proposal is safe (the
# round-trip gate filters), so an ambiguous weak ``t`` yields both ``t`` (from
# ``d``? no --- ``d`` is the weak of ``t``) pre-images.
_WEAK_TO_STRONG: tuple[tuple[str, str], ...] = (
    ("k", "kk"),
    ("p", "pp"),
    ("t", "tt"),
    ("nn", "nt"),
    ("ll", "lt"),
    ("rr", "rt"),
    ("mm", "mp"),
    ("ng", "nk"),
    ("d", "t"),
    ("v", "p"),
)


def _strong_grade_preimages(weak: str) -> set[str]:
    """Return strong-grade pre-images of a weak-grade consonant part.

    Inverts the forward :data:`gradation._CLUSTER_RULES`: for each weak ending
    that a forward rule could have produced, propose the strong cluster
    (``hallinno``-stem consonant ``hallinn`` -> ``hallint``; ``kohda``-stem
    ``kohd`` -> ``koht``; ``momenti`` consonant ``moment`` -> ``momentt``).
    Over-proposal is safe --- the round-trip gate discards pre-images whose
    forward generation does not reproduce the surface.
    """
    out: set[str] = set()
    for weak_end, strong_end in _WEAK_TO_STRONG:
        if weak.endswith(weak_end):
            out.add(weak[: -len(weak_end)] + strong_end)
    return out


def _nominative_classes(surface: str) -> set[str]:
    """Morph_classes whose NOMINATIVE shape is consistent with ``surface``.

    Mirrors the ending constraints the class builders impose on a citation form,
    so the bare surface is only proposed as a nominative lemma under classes that
    could actually have it as a nominative (``-Us`` needs an ``-us`` ending, etc).
    Pure structural pruning --- the round-trip gate still verifies.
    """
    out: set[str] = set()
    if not surface:
        return out
    if surface.endswith("nen"):
        out.add("-nen")
    if surface.endswith(("us", "ys")):
        out.add("-Us->-Ukse-")
    if surface.endswith(("uus", "yys")):
        out.add("-Uus->-Ude-")
    if surface.endswith(("os", "ös")):
        out.add("-Os->-Okse-")
    if surface.endswith("i"):
        out.add("-i->-e-")
    if surface.endswith("e"):
        out.add("e_contract")
    if surface[-1] in _VOWELS:
        out.add("vowel_final")
    return out


def _singular_stem_lemmas(stem: str) -> set[tuple[str, str]]:
    """Invert each M1 stem-class builder on a SINGULAR stem -> (lemma, class).

    For a stem exposed by suffix stripping, each forward class rule that *could*
    have produced it yields a candidate lemma.  Many-to-one forward rules (e.g.
    gradation collapsing distinct clusters onto one weak stem) are handled by
    emitting all plausible pre-images and letting the round-trip gate verify ---
    we do NOT need to perfectly invert gradation here, only to propose the lemma
    whose forward generation will reproduce the surface.
    """
    out: set[tuple[str, str]] = set()
    if not stem:
        return out

    last = stem[-1]

    # vowel_final: oblique stem = weak(consonant) + final_vowel; the lemma is the
    # STRONG-grade nominative.  We cannot un-gradate deterministically, so we
    # propose the stem itself as the lemma (covers non-gradating members and the
    # single-k=zero members where weak==strong-minus-k is recovered by trying the
    # +k lemma below); the gate verifies.
    if last in _VOWELS:
        consonant_part = stem[:-1]  # the (possibly weak-grade) consonant part
        final_vowel = last
        out.add((stem, "vowel_final"))  # non-gradating member: weak == strong
        # Gradating member: the exposed consonant part is the WEAK grade; restore
        # every strong-grade pre-image of its final cluster (nt<-nn, tt<-t, ...).
        for strong in _strong_grade_preimages(consonant_part):
            out.add((strong + final_vowel, "vowel_final"))
        # single-k zero realization: laki -> lai- (weak drops the k).  Propose
        # re-inserting a k before the final vowel (lai -> laki), plus the v/j
        # realizations' pre-images (luvu- -> luku, ...).
        out.add((consonant_part + "k" + final_vowel, "vowel_final"))
        if consonant_part and consonant_part[-1] in "vj":
            out.add((consonant_part[:-1] + "k" + final_vowel, "vowel_final"))

    # -Us/-Os -> -Ukse-/-Okse-: oblique/vowel stem = base + "kse".  Invert:
    # drop "kse", restore the final "s" -> lemma (asetukse -> asetus).
    if stem.endswith("kse"):
        base = stem[:-3]
        out.add((base + "s", "-Us->-Ukse-"))
        out.add((base + "s", "-Os->-Okse-"))

    # A consonant-final -s stem is the bare lemma of an -Us/-Os/-Uus noun: the
    # partitive (-tA) and the plural genitive (-ten) attach straight to the
    # nominative (asetus -> asetusta / asetusten; oikeus -> ...).  Propose the
    # stem itself as the lemma under each -s class.
    if last == "s":
        out.add((stem, "-Us->-Ukse-"))
        out.add((stem, "-Os->-Okse-"))
        out.add((stem, "-Uus->-Ude-"))

    # -Uus -> -Ude-: stem = base + "de".  Invert -> base + "s" (oikeude->oikeus).
    if stem.endswith("de"):
        out.add((stem[:-2] + "s", "-Uus->-Ude-"))
    # -Uus illative_stem = base + "te" (oikeute-): same lemma.
    if stem.endswith("te"):
        out.add((stem[:-2] + "s", "-Uus->-Ude-"))

    # e_contract: inflected stem = (strengthened consonant) + "ee".  Invert ->
    # nominative is the weak grade + bare "e": liitte -> liite, ohjee -> ohje,
    # Tamperee -> Tampere.  Many gradation pre-images -> propose the obvious one
    # plus the degeminated one; the gate verifies.
    if stem.endswith("ee"):
        out.add((stem[:-1], "e_contract"))            # ohjee -> ohje
        core = stem[:-2]                              # consonant part
        if len(core) >= 2 and core[-1] == core[-2]:
            out.add((core[:-1] + "e", "e_contract"))  # liitte -> liite (degeminate)

    # -i->-e-: inflected stem = weak(consonant) + "e".  Invert -> consonant + "i"
    # (kaare -> kaari).
    if last == "e" and len(stem) >= 2:
        out.add((stem[:-1] + "i", "-i->-e-"))
        # An -e final stem can also be an e_contract NOMINATIVE itself (its
        # nominative is the bare lemma, ohje): the plural part/gen build off the
        # shortened ohje- stem, so the stem here equals the lemma.
        out.add((stem, "e_contract"))

    # -nen -> -se-: stem = base + "se".  Invert -> base + "nen".
    if stem.endswith("se"):
        out.add((stem[:-2] + "nen", "-nen"))

    return out


def _plural_stem_lemmas(stem: str) -> set[tuple[str, str]]:
    """Invert a PLURAL ``weak_i_stem`` (ends in -i-) -> (lemma, class).

    The plural INE/ELA attach -ssA/-stA to ``weak_i_stem`` (the -i- marker is the
    last char of the stem, e.g. ``asetuksi`` -> ``asetuksissa``).  Strip the -i-
    marker and route the residual through the plural-tail singular-stem inversion,
    then through :func:`_singular_stem_lemmas`.
    """
    out: set[tuple[str, str]] = set()
    if not stem or stem[-1] != "i":
        return out
    for sg_stem in _plural_tail_to_singular_stems(stem):
        out.update(_singular_stem_lemmas(sg_stem))
    return out


def _illative_stem_lemmas(norm: str) -> set[tuple[str, str]]:
    """Recover lemmas for the singular illative (-Vn doubled vowel, or -seen)."""
    out: set[tuple[str, str]] = set()
    # -seen (long-vowel / contracted stems: Tampereeseen).
    if norm.endswith("seen") and len(norm) > 4:
        stem = norm[:-4]  # the vowel stem before -seen (Tamperee)
        out.update(_singular_stem_lemmas(stem))
    # -Vn : final vowel doubled + n (lakiin, virastoon, asetukseen, kaareen).
    if norm.endswith("n") and len(norm) >= 3:
        body = norm[:-1]
        if len(body) >= 2 and body[-1] in _VOWELS and body[-1] == body[-2]:
            stem = body[:-1]  # undo the lengthening -> vowel stem (laki->lakii->laki)
            out.update(_singular_stem_lemmas(stem))
    return out


def _partitive_stem_lemmas(norm: str) -> set[tuple[str, str]]:
    """Recover lemmas for the singular partitive (-A / -tA / -ttA)."""
    out: set[tuple[str, str]] = set()
    # -ttA : oikeutta (base = oikeu -> -Uus lemma oikeus), Tamperetta (base =
    # Tampere -> e_contract lemma Tampere).
    for suf in ("tta", "ttä"):
        if norm.endswith(suf) and len(norm) > len(suf):
            base = norm[: -len(suf)]
            out.update(_singular_stem_lemmas(base))
            out.update(_singular_stem_lemmas(base + "e"))
            # -Uus partitive: the -ttA attaches to the bare stem vowel
            # (oikeu + tta), so the lemma is base + "s" (oikeus).
            out.add((base + "s", "-Uus->-Ude-"))
            # e_contract partitive (Tamperetta): the -ttA attaches to the
            # nominative, so base is itself the lemma (Tampere).
            out.add((base, "e_contract"))
    # -tA : asetusta (stem asetus, consonant-final), kaarta (-i->-e- type), -nen.
    for suf in ("ta", "tä"):
        if norm.endswith(suf) and len(norm) > len(suf):
            base = norm[: -len(suf)]
            out.update(_singular_stem_lemmas(base))
            # -i->-e- partitive attaches -ta to the bare consonant stem (kaar):
            # propose the -i lemma directly (kaar -> kaari).
            out.add((base + "i", "-i->-e-"))
            # -nen partitive (-sta restored): base ends in s -> nen lemma.
            if base.endswith("s"):
                out.add((base[:-1] + "nen", "-nen"))
    # -A (single short vowel): lakia, virastoa.  Drop the final vowel -> stem.
    if norm and norm[-1] in "aä" and len(norm) >= 2:
        out.update(_singular_stem_lemmas(norm[:-1]))
    return out


def _plural_tail_to_singular_stems(tail: str) -> set[str]:
    """Map a plural tail (after stripping the case ending) to singular stem(s).

    The plural ``-i-``/``-j-`` marker sits between stem and ending and triggers a
    small set of stem-vowel transforms (see :mod:`plurals`).  We propose the
    inverse transforms; the round-trip gate verifies which (if any) reproduces
    the surface, so over-proposing is safe.
    """
    out: set[str] = set()
    if not tail:
        return out
    last = tail[-1]

    # The tail might already BE the singular stem (no -i- marker): the legal
    # -ten / -den plural genitive attaches straight to the nominative
    # (asetus -> asetusten; ohjei... no).  Always include it.
    out.add(tail)

    # Marker -i- on a consonant stem (vowel dropped / -i->-e- consonant stem):
    # tail ends in i -> the stem is tail with the i removed, optionally with a
    # restored final vowel.
    if last == "i":
        cons = tail[:-1]
        out.add(cons)              # consonant stem (kaar, asetuks)
        out.add(cons + "i")        # i-final lemma (laki, momentti)
        out.add(cons + "e")        # e-restored (ohjei -> ohje)
        for v in "aäoöuy":
            out.add(cons + v)      # restore a dropped final vowel (pykäl -> pykälä)
        # i->e words: lae- (weak) reflects laki; propose the -i nominative.
        if cons and cons[-1] == "e":
            out.add(cons[:-1] + "i")

    # Marker realized as -j- between vowels: tail ends in the kept vowel directly
    # (the -jA/-jen endings strip to the strong vowel stem itself, virasto-).
    if last in _VOWELS:
        # a->o plural pre-image: kalo- could be kala; propose the -a lemma.
        if last == "o":
            out.add(tail[:-1] + "a")
        # i->e plural part/gen stem (lake-/lakei-): the lemma is the -i form.
        if last == "e":
            out.add(tail[:-1] + "i")

    # Consonant-final tail (pykäl, lak, kaar, asetuks): the singular stem may add
    # a dropped final vowel (pykäl -> pykälä), an -i (lak -> laki, kaar -> kaari),
    # an -e (ohj -> ?), or restore an -s nominative (asetuks -> asetus).
    if last not in _VOWELS:
        out.add(tail + "i")        # lak -> laki, kaar -> kaari, direktiiv -> direktiivi
        out.add(tail + "e")
        for v in "aäoöuy":
            out.add(tail + v)      # pykäl -> pykälä
        # -Us/-Os/-Uus consonant stem (asetuks -> asetus): drop the k, add s.
        if tail.endswith("ks"):
            out.add(tail[:-2] + "s")

    return out


__all__ = ["MorphAnalysis", "analyze_open"]
