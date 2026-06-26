"""Statute-NAME -> statute-id registry (R5a / M2 substrate, Index B).

Named-statute citations in body prose --- ``Holhouslaki``, ``Vaalilain``,
``Ulosottolain ...`` --- are currently almost never resolved because the cite
carries no ``(NNN/YYYY)`` anchor, only an inflected title.  This registry is the
resolution substrate for those cites.

Design (per ``FI_MORPHOLOGY_DESIGN_DECISION.md`` Index B):

* **Generation-first.**  The registry is built from a set of canonical
  ``(statute_id, canonical_title, valid_from, valid_to)`` entries.  A Finnish
  statute title is a compound whose *head* (``laki`` / ``asetus`` / ``paatos``
  ...) carries the inflection while the *modifier* prefix rides invariant
  (``Holhous`` + ``laki`` -> ``Holhouslaki``; genitive head ``lain`` ->
  ``Holhouslain``).  We split off the trailing known head, run the merged M1
  morphology engine (``generate_forms``) over that head, and re-attach the
  invariant modifier to every generated head form --- producing the inflected
  surface variants WITHOUT storing form tables.

* **Fail-loud, temporal.**  A title can name different acts over time (an act is
  repealed and re-enacted under the same name).  ``lookup`` therefore returns a
  typed :class:`RegistryResult`: after the ``as_of`` filter, ``>1`` surviving
  candidate -> ``status="multiple"`` (NEVER silently pick the newest); ``0`` ->
  ``status="none"``; exactly ``1`` -> ``status="single"``.  Convention:
  **static-as-of-citing** --- ``as_of`` is the validity instant the citation is
  read against; a citation with no ``as_of`` is resolved against the entire
  timeline and is allowed to be ambiguous.

* **Index B authoritative for identity; M1 supplies only inflection.**  When the
  head is not a closed-class head the engine cannot inflect it; we still register
  the nominative (uninflected) surface so an exact-title cite resolves, and we
  fail loud (no inflected variants) rather than guess.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from lawvm.finland.morphology import (
    MorphEntry,
    MorphNumber,
    analyze_open,
    classify,
    generate_forms,
    head_entry,
    is_known_head,
)
from lawvm.finland.references.registries.statute_name_aliases import (
    STATUTE_NAME_ALIASES,
    StatuteNameAlias,
)

# Closed statute/instrument heads (mirrors the morphology head table's
# statute_head instrument heads).  Sorted longest-first so a title ending in
# ``...asetus`` is split on ``asetus`` and never on a shorter coincidental
# suffix, making the modifier/head split unambiguous.
_HEADS_BY_LEN: tuple[str, ...] = tuple(
    sorted(
        (
            "laki",
            "asetus",
            "päätös",
            "sopimus",
            "säädös",
            "määräys",
            "ohje",
            "ilmoitus",
            "direktiivi",
        ),
        key=len,
        reverse=True,
    ),
)


@dataclass(frozen=True, slots=True)
class StatuteNameEntry:
    """A canonical name->id binding with its validity window.

    ``valid_from``/``valid_to`` are inclusive-start / exclusive-end (``None`` =
    open).  ``valid_to=None`` means "still current".
    """

    statute_id: str
    canonical_title: str
    valid_from: Optional[dt.date] = None
    valid_to: Optional[dt.date] = None

    def covers(self, as_of: dt.date) -> bool:
        """Whether this entry is in force at ``as_of`` (start-inclusive)."""
        if self.valid_from is not None and as_of < self.valid_from:
            return False
        if self.valid_to is not None and as_of >= self.valid_to:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Candidate:
    """A single resolution candidate behind a looked-up surface."""

    statute_id: str
    canonical_title: str
    valid_from: Optional[dt.date]
    valid_to: Optional[dt.date]


@dataclass(frozen=True, slots=True)
class RegistryResult:
    """Typed outcome of :meth:`StatuteNameRegistry.lookup`.

    ``registry_status`` is the fail-loud control signal:

    * ``single``   --- exactly one candidate survives the ``as_of`` filter.
    * ``multiple`` --- the surface names more than one act (over time); ALL are
      listed in ``candidates`` and none is silently chosen.
    * ``none``     --- the surface is unknown (or no candidate is in force at
      ``as_of``).
    """

    registry_status: str  # single | multiple | none
    candidates: tuple[Candidate, ...] = ()
    surface: str = ""
    as_of: Optional[dt.date] = None


def _split_head(title: str) -> tuple[str, str] | None:
    """Split a canonical title into ``(modifier, head_lemma)``.

    Returns the invariant modifier prefix (original casing preserved) and the
    closed-class head lemma (lowercase) the title ends with, or ``None`` if the
    title ends in no known statute head.
    """
    low = title.lower()
    for head in _HEADS_BY_LEN:
        if low.endswith(head):
            modifier = title[: len(title) - len(head)]
            return modifier, head
    return None


# ---------------------------------------------------------------------------
# Compound-nickname derivation (recall lever for "Laki X:stä cited as Xlaki").
# ---------------------------------------------------------------------------
#
# A pervasive Finnish legal-language phenomenon: an act is *titled* "Laki
# <NOUN-in-elative>" / "Asetus <NOUN-in-elative>" ("Laki verotusmenettelystä")
# but *cited* by a COMPOUND nickname gluing the noun's nominative to the head
# ("verotusmenettelylaki" -> cited "verotusmenettelylain").  The citation key the
# by-name recognizer produces ("verotusmenettelylaki") therefore never matches
# the official title's generated keys ("verotusmenettelystä" ...), and the act
# silently misses.  Bucket (d) of ``tools.resolution_miss_analysis``.
#
# To recover this we must reverse the noun's oblique case (elative ``-sta/-stä``)
# back to the nominative — a REVERSE morphology step the generation-only M1 engine
# does NOT provide.  Reverse inflection is in general ambiguous (consonant
# gradation: ``kunta`` -> stem ``kunna-`` -> ``kunnasta``; ``jako`` -> ``jaosta``;
# ``kauppa`` -> ``kaupasta``), and a WRONG nickname is worse than a miss (it can
# resolve a genuine citation to the wrong act).  So this is deliberately a
# BOUNDED, CONSERVATIVE stripper that only fires on the unambiguously reversible
# subset and SKIPS (fail-loud) everything else:
#
#   * Title shape must be exactly ``Laki <one elative word>`` or
#     ``Asetus <one elative word>`` (single noun, no further phrase).  Multi-word
#     titles ("Laki yleisistä kokouksista", "Laki Korkeimmasta oikeudesta") and
#     amendment titles ("... annetun lain muuttamisesta") are NOT nicknameable and
#     are skipped.
#   * The word must end in the elative ``-sta``/``-stä``; stripping it must leave a
#     VOWEL-final candidate stem (the productive vowel-stem declension where the
#     nominative equals the inflectional stem, e.g. ``apteekkimaksu`` ->
#     ``apteekkimaksusta``).
#   * The candidate's FINAL-SYLLABLE ONSET must be GRADATION-IMMUNE: it must not
#     contain a stop ``k/p/t`` (qualitative/quantitative gradation, ``jako`` ->
#     ``jaosta``) nor a doubled nasal/liquid ``nn/mm/ll/rr/ng/nk`` (which is the
#     WEAK grade of an underlying ``nt/mp/lt/rt/nk`` we cannot recover, ``kunta``
#     -> ``kunnasta``).  These environments are where reverse inflection is
#     ambiguous; we refuse to guess.
#   * The candidate is then VERIFIED by the forward M1 engine: the engine must
#     GENERATE the title's exact elative surface from the candidate nominative
#     (vowel-harmony-folded comparison).  Only a candidate the generator confirms
#     becomes a nickname; an unclassifiable or non-confirming candidate is skipped.
#
# The derived nickname carries NO new identity: it is just another generated
# surface key for the SAME ``statute_id``.  When two acts over time derive the
# same nickname (an act re-enacted under a renamed compound title) the registry
# lands ``status="multiple"`` (ambiguous) — the safe fail-loud outcome, never a
# silent pick.

_VOWELS = "aeiouyäö"


def _harmony_fold(s: str) -> str:
    """Fold front/back vowel-harmony pairs so a stem can be compared ignoring it.

    The candidate nominative is sliced out of the (ground-truth) title word, so
    its stem identity is what matters; the M1 generator's harmony handling is
    independent and occasionally diverges, so the verification compares on a
    harmony-folded basis (``ä->a``, ``ö->o``, ``y->u``).
    """
    return s.replace("ä", "a").replace("ö", "o").replace("y", "u")


def _final_syllable_onset(stem: str) -> Optional[str]:
    """Return the consonant onset cluster of ``stem``'s final syllable.

    The onset is the consonant run between the penultimate and final vowels
    (``apteekkimaksu`` -> final vowel ``u``, preceding ``ks`` -> onset ``ks``;
    ``hallinno`` -> ``nn``; ``jao`` -> ``""`` (vowel-initial final syllable,
    a k-deletion gradation signature)).  ``None`` when there is no vowel.
    """
    i = len(stem) - 1
    while i >= 0 and stem[i] not in _VOWELS:
        i -= 1
    if i < 0:
        return None
    j = i - 1
    while j >= 0 and stem[j] not in _VOWELS:
        j -= 1
    return stem[j + 1 : i]


def _gradation_ambiguous(onset: str) -> bool:
    """True when ``onset`` is a gradation environment we cannot safely reverse.

    A stop ``k/p/t`` (qualitative or quantitative gradation), an empty onset (the
    vowel-initial final syllable left by k-deletion: ``jako`` -> ``jaosta``), or a
    weak-grade doubled nasal/liquid (``nn/mm/ll/rr/ng/nk``, the weak grade of
    ``nt/mp/lt/rt/nk``).  In all of these the nominative is not recoverable from
    the oblique stem, so the nickname is skipped (fail-loud, never guessed).
    """
    if onset == "":
        return True
    if any(c in onset for c in "kpt"):
        return True
    for weak in ("nn", "mm", "ll", "rr", "ng", "nk"):
        if weak in onset:
            return True
    return False


def _verify_generates_elative(candidate: str, target_word_folded: str) -> bool:
    """True iff the M1 generator produces ``target_word_folded`` from ``candidate``.

    The candidate nominative is classified and run forward through the SG case
    generator; if any deterministically generated surface (harmony-folded) equals
    the title's elative word (harmony-folded) the reverse step is confirmed.  An
    unclassifiable candidate (``classification_status != "resolved"``) returns False — we never
    index an unverified nickname.
    """
    cls = classify(candidate)
    if cls.classification_status != "resolved" or cls.morph_class is None:
        return False
    entry = MorphEntry(
        lemma_id=candidate,
        lemma=candidate,
        referent_kind="common",
        morph_class=cls.morph_class,
        gradation=False,
    )
    for form in generate_forms(entry, numbers=(MorphNumber.SG,)):
        if form.certainty != "deterministic" or not form.surface:
            continue
        if _harmony_fold(form.surface.lower()) == target_word_folded:
            return True
    return False


def derive_nicknames(title: str) -> list[str]:
    """Derive conservative compound-nickname keys for a ``Laki/Asetus X:stä`` title.

    Returns the normalized lower-cased nickname key(s) (e.g.
    ``["verotusmenettelylaki"]`` for ``"Laki verotusmenettelystä"``), or ``[]``
    when the title is not of the cleanly-reversible single-noun-elative shape (see
    the module-section comment for the exact boundary).  The derivation is bounded
    and verified: it never guesses an irregular/gradating reverse-inflection.
    """
    low = " ".join(title.strip().rstrip(".").lower().split())
    # Only "Laki <word>" / "Asetus <word>" — exactly one noun after the head word.
    # (A title already ending in a known head is handled by inflection, not here;
    # an amendment / multi-word title is not nicknameable.)
    parts = low.split(" ")
    if len(parts) != 2:
        return []
    head, word = parts
    if head not in ("laki", "asetus"):
        return []
    if not (word.endswith("sta") or word.endswith("stä")):
        return []
    if not word[:-3].isalpha():  # the noun must be a single alphabetic token
        return []
    candidate = word[:-3]
    if not candidate or candidate[-1] not in _VOWELS:
        return []  # only vowel-stem nouns (nominative == inflectional stem)
    onset = _final_syllable_onset(candidate)
    if onset is None or _gradation_ambiguous(onset):
        return []  # reverse inflection ambiguous here — skip, never guess
    if not _verify_generates_elative(candidate, _harmony_fold(word)):
        return []  # the forward generator does not confirm the reverse step
    return [(candidate + head)]


def _strip_title_terminator(title: str) -> str:
    """Strip a trailing sentence terminator (``.``) from a canonical title.

    Many older Finlex titles are stored with a trailing full stop
    (``Palolaki.``, ``Verotuslaki.``, ``Laki eräistä naapuruussuhteista.``).
    The terminator is orthography, not part of the act's NAME — but it breaks
    BOTH the head split (``palolaki.`` does not ``endswith('laki')``, so NO
    inflected surfaces are generated and only the literal nominative-with-period
    key is registered) AND every cite match (a citation ``palolain`` carries no
    period). Stripping it is a pure punctuation normalization: it does not invent
    or alter any name token, it only removes the terminator so the real
    head-split + inflection runs. A title that is nothing but punctuation (never
    seen in the corpus) collapses to empty and is handled by the caller as
    before.
    """
    return title.rstrip().rstrip(".").rstrip()


def _inflected_surfaces(title: str) -> dict[str, str]:
    """Map every generated surface variant of ``title`` -> its normalized key.

    Generation-first: split off the closed head, inflect the head with the M1
    engine, re-attach the invariant modifier.  Always includes the nominative
    title itself.  If the title has no known head (cannot be inflected) only the
    nominative surface is registered --- fail loud, never guess inflection.

    A trailing sentence terminator (``Palolaki.``) is stripped first (see
    :func:`_strip_title_terminator`): it is orthography, not a name token, and
    leaving it on blocks the head split AND every period-free citation. The
    stripped title is what is keyed/inflected; the entry keeps the original
    ``canonical_title`` for display.

    Returns ``{normalized_surface_key: display_surface}``.
    """
    out: dict[str, str] = {}

    def _add(surface: str) -> None:
        key = _normalize_key(surface)
        if key:
            out[key] = surface

    def _inflect_head_bearing(modifier: str, head: str) -> None:
        """Add every SG-inflected surface of a ``modifier + head`` compound."""
        if not is_known_head(head):
            return
        entry = head_entry(head)
        for form in generate_forms(entry, numbers=(MorphNumber.SG,)):
            if form.certainty != "deterministic" or not form.surface:
                continue
            _add(modifier + form.surface)

    title = _strip_title_terminator(title)

    _add(title)

    split = _split_head(title)
    if split is not None:
        modifier, head = split
        _inflect_head_bearing(modifier, head)

    # Compound-nickname recall lever: a "Laki/Asetus <noun>:stä" title is also
    # CITED as the compound "<noun-nominative>laki"/"...asetus".  Derive that
    # nickname (bounded + verified, see ``derive_nicknames``) and index it as a
    # head-bearing compound under the SAME id — its nominative key is what the
    # by-name recognizer normalizes such a citation to, plus its inflected forms
    # for any exact-surface lane.
    for nickname in derive_nicknames(title):
        nick_split = _split_head(nickname)
        if nick_split is None:  # pragma: no cover - derive_nicknames always heads
            _add(nickname)
            continue
        nmod, nhead = nick_split
        _add(nickname)
        _inflect_head_bearing(nmod, nhead)
    return out


def _normalize_key(surface: str) -> str:
    """Case/space-fold a surface into its lookup key."""
    return " ".join(surface.lower().split())


# ---------------------------------------------------------------------------
# Content-word-set title index (inflection-robust descriptive-title matching).
# ---------------------------------------------------------------------------
#
# A head-first descriptive cite (``viranomaisten toiminnan julkisuudesta annetun
# lain``) is reconstructed by the by-name lane head-first as ``laki <complement>``
# — the SAME surface the registry indexes for the official title ``Laki
# viranomaisten toiminnan julkisuudesta``. Exact-key matching resolves it WHEN the
# complement is byte-identical. But a citing author often inflects a PREMODIFIER
# differently from the official title (singular ``viranomaisen`` vs official plural
# ``viranomaisten``; ``luopumiskorvauksista`` PL vs official ``luopumiskorvauksesta``
# SG), so the exact key differs and the act misses.
#
# The content-word-set index keys a BASE-ACT descriptive title on the SET of its
# content-word STEMS (function words dropped; each word reduced to the shortest
# M1-round-trippable lemma stem). The SAME reduction applied to the citation's
# complement collapses many inflection differences onto the same set, so the cite
# resolves. This is a STRICT FP-GATED FALLBACK consulted only after the exact-key
# lookup has missed (see resolve.py): it never overrides an exact match, requires a
# WHOLE-SET match (not a loose subset), requires the cite head (laki/asetus) to
# match the title head (a laki cite never resolves to an asetus act), and refuses a
# garbage / under-specified complement. >1 candidate is AMBIGUOUS (never picked).
#
# Discipline note: this is a recall lever, NOT a relaxation of fail-loud. The set
# match is a deterministic function applied identically to both sides; a stem that
# does not collapse a pair leaves them apart (a miss, never a wrong resolution).

# Function words / coordinators that carry no act-identity content and are dropped
# from the set. ``ja``/``sekä``/``tai`` (coordinators) are dropped so a coordinated
# title and its citation key on the same content words; the closed determiner /
# adverb / conjunction set below is the EXTRACTION-NOISE guard — a complement that
# CONTAINS one of these (other than a bare coordinator between content words) is
# clause-leak garbage from the by-name complement walk, not a clean title, and is
# refused entirely (``_content_complement_is_clean``).
_CWS_COORDINATORS: frozenset[str] = frozenset({"ja", "sekä", "tai", "eli"})

# Tokens whose PRESENCE in a reconstructed complement marks it as extraction noise
# (a clause / determiner / adverb leak from the by-name complement walk), e.g.
# ``laki mitä valtioneuvoston …``, ``laki osin eläinten …``, ``laki kun
# finanssivalvonnasta``, ``laki perustuvan sosiaaliturvalainsäädännön …``. A
# complement containing any of these is refused (no set is built), so a garbage
# complement can never coincidentally hit a registered set.
_CWS_GARBAGE_TOKENS: frozenset[str] = frozenset(
    {
        # determiners / pronouns
        "mitä", "mikä", "joka", "jonka", "jota", "jossa", "se", "sitä", "sen",
        "tämä", "tämän", "tätä", "tuo", "tuon", "niitä", "niiden",
        # adverbs / conjunctions frequently leaked by the complement walk
        "kun", "jos", "vaikka", "koska", "siten", "kuin", "vain", "osin",
        "myös", "sekä", "mutta", "että", "kuitenkin",
        # stray participles that signal a clause boundary, not a title head
        "perustuvan", "koskevan", "annetun", "annetussa", "annettu",
    }
)

# A title-content set must carry at least this many content-word stems to be a
# distinctive identity. A single-stem set (``laki edistämisestä`` ->
# ``{edistäminen}``) is too generic — many acts share one common-noun head, so a
# 1-stem match is high-FP. The exact-key lane already resolves clean single-noun
# titles, so the content-word fallback can safely require >=2 distinctive stems.
_CWS_MIN_STEMS = 2


def _content_word_stem(word: str) -> str:
    """Reduce one title/complement word to its shortest M1-round-trippable stem.

    The open-vocabulary analyzer returns every lemma that round-trips to the
    surface; the SHORTEST is the most aggressive (inflection-stripped) stem, and
    applying it IDENTICALLY to both the registry title word and the citation word
    collapses many singular/plural and case differences onto one key. A surface the
    analyzer cannot invert (an unknown / non-Finnish token) keeps its own surface —
    a conservative fallback that still matches an identical surface on both sides.
    """
    cands = analyze_open(word)
    if cands:
        lemmas = {c.lemma for c in cands}
        return min(lemmas, key=lambda s: (len(s), s))
    return word


def _content_complement_is_clean(complement: str) -> bool:
    """True when ``complement`` is a clean descriptive-title body (no leak noise).

    Refuses (returns ``False``) a complement that contains an extraction-noise
    token (a determiner / adverb / conjunction / stray participle from the by-name
    complement walk — see ``_CWS_GARBAGE_TOKENS``). A clean complement is the body
    of a real descriptive title (``viranomaisten toiminnan julkisuudesta``); a
    leaked clause (``mitä valtioneuvoston kanslian tehtävistä valtioneuvostosta``,
    ``kun finanssivalvonnasta``) is not.
    """
    words = [w.strip(".,:;§") for w in complement.lower().split()]
    return not any(w in _CWS_GARBAGE_TOKENS for w in words if w)


def content_word_set(complement: str) -> frozenset[str]:
    """Content-word-STEM set of a descriptive-title body, or empty if not usable.

    Drops coordinators / very short tokens, reduces each remaining word to its
    shortest M1 stem (:func:`_content_word_stem`), and returns the resulting set.
    Returns the EMPTY set (an explicit "no usable content set" signal the caller
    treats as a non-match) when the complement is extraction-noise
    (:func:`_content_complement_is_clean`) or carries fewer than
    :data:`_CWS_MIN_STEMS` distinctive stems. The reduction is deterministic and
    applied identically to titles (at index build) and citations (at lookup).
    """
    if not _content_complement_is_clean(complement):
        return frozenset()
    stems: set[str] = set()
    for raw in complement.lower().split():
        w = raw.strip(".,:;§")
        if not w or w in _CWS_COORDINATORS or len(w) < 3:
            continue
        stems.add(_content_word_stem(w))
    if len(stems) < _CWS_MIN_STEMS:
        return frozenset()
    return frozenset(stems)


def _title_content_key(title: str) -> tuple[str, frozenset[str]] | None:
    """Build the ``(head, content-word-set)`` index key for a BASE-ACT title.

    Returns ``None`` for any title that is NOT a clean head-first base-act
    descriptive title: a non-``Laki``/``Asetus`` title, an AMENDMENT / REPEAL title
    (``… annetun lain muuttamisesta``, ``… kumoamisesta``), or a title whose body
    yields no usable content set. The head (``laki``/``asetus``) is carried IN the
    key so a ``laki`` citation can never match an ``asetus`` act of the same
    subject (a real distinction: a law and a decree on the same matter are
    different statutes).
    """
    low = _strip_title_terminator(title).lower()
    parts = low.split(" ", 1)
    if len(parts) != 2 or parts[0] not in ("laki", "asetus"):
        return None
    head, complement = parts
    # Amendment / repeal titles name an amending act, not a base identity; the
    # descriptive cite ``X annetun lain`` denotes the BASE act, so these must not
    # be indexed (else a cite would resolve to an amendment statute).
    if (
        "muuttamisesta" in complement
        or "kumoamisesta" in complement
        or "muuttamista" in complement
        or "annetun" in complement
    ):
        return None
    cws = content_word_set(complement)
    if not cws:
        return None
    return head, cws


def citation_content_key(fi_name_key: str) -> tuple[str, frozenset[str]] | None:
    """Build the ``(head, content-word-set)`` lookup key for a ``fi-name:`` payload.

    ``fi_name_key`` is the normalized payload the by-name lane mints, head-first for
    descriptive cites (``laki viranomaisten toiminnan julkisuudesta``). Returns the
    same shape as :func:`_title_content_key`, or ``None`` when the cite is not a
    head-first ``Laki/Asetus <body>`` shape or its body is garbage / too generic.
    A compound-nickname key (``verotuslaki``) is NOT head-first (no space), so it
    returns ``None`` and is left to the exact-key lane — the content-word fallback
    is for the multi-word descriptive shape only.
    """
    parts = fi_name_key.split(" ", 1)
    if len(parts) != 2 or parts[0] not in ("laki", "asetus"):
        return None
    head, complement = parts
    cws = content_word_set(complement)
    if not cws:
        return None
    return head, cws


class StatuteNameRegistry:
    """Surface (possibly inflected) -> statute id(s), with temporal resolution.

    Built generation-first by :func:`build_registry`.  Resolution is fail-loud:
    a surface that names several acts over time yields ``status="multiple"``;
    the caller decides, the registry never picks.
    """

    __slots__ = ("_index", "_content_index")

    def __init__(self) -> None:
        # normalized surface key -> list of entries (one per (id, window) that
        # generates this surface).  A surface may appear under several entries.
        self._index: dict[str, list[StatuteNameEntry]] = {}
        # (head, content-word-set) -> list of base-act entries.  The FP-gated
        # inflection-robust fallback index (see ``lookup_content_word_set``); only
        # clean head-first base-act descriptive titles are indexed here.
        self._content_index: dict[
            tuple[str, frozenset[str]], list[StatuteNameEntry]
        ] = {}

    def _register(self, entry: StatuteNameEntry) -> None:
        for key in _inflected_surfaces(entry.canonical_title):
            self._register_key(key, entry)
        content_key = _title_content_key(entry.canonical_title)
        if content_key is not None:
            self._register_content_key(content_key, entry)

    def _register_content_key(
        self,
        key: tuple[str, frozenset[str]],
        entry: StatuteNameEntry,
    ) -> None:
        """Bind a ``(head, content-word-set)`` key to ``entry`` (idempotent).

        Same dedup discipline as :meth:`_register_key`: a colliding (id, window)
        is deduped, and a content set shared by a DIFFERENT id accumulates so the
        content-word lookup lands ``multiple`` (fail-loud, never picked).
        """
        bucket = self._content_index.setdefault(key, [])
        sig = (entry.statute_id, entry.valid_from, entry.valid_to)
        if all((e.statute_id, e.valid_from, e.valid_to) != sig for e in bucket):
            bucket.append(entry)

    def _register_key(self, key: str, entry: StatuteNameEntry) -> None:
        """Bind one already-normalized surface ``key`` to ``entry`` (idempotent).

        The shared path behind both generation (``_register``) and the curated
        alias table (``register_aliases``): a colliding (id, window) is deduped,
        and a key shared by a DIFFERENT id naturally accumulates so ``lookup``
        lands ``multiple`` (fail-loud) — an alias never silently overrides.
        """
        bucket = self._index.setdefault(key, [])
        # Dedup on (id, window) so re-registering the same act is idempotent.
        sig = (entry.statute_id, entry.valid_from, entry.valid_to)
        if all((e.statute_id, e.valid_from, e.valid_to) != sig for e in bucket):
            bucket.append(entry)

    def register_aliases(self, aliases: Iterable[StatuteNameAlias]) -> None:
        """Index each curated colloquial nickname as a surface key for its act.

        Unlike :meth:`_register`, an alias key is NOT expanded through the
        generation engine: the nickname is by construction not derivable from the
        official title (that is why it misses), so it is bound DIRECTLY under its
        normalized key to a :class:`StatuteNameEntry` carrying the act's id and
        official title.  Validity is left open (``None``) — the curated table only
        admits nicknames with a single corpus identity, so a window is unneeded.
        Coexists with generated surfaces and stays fail-loud on any cross-id
        collision (the shared ``_register_key`` path).
        """
        for alias in aliases:
            entry = StatuteNameEntry(
                statute_id=alias.statute_id,
                canonical_title=alias.official_title,
            )
            self._register_key(_normalize_key(alias.alias_key), entry)

    @staticmethod
    def _result_from_bucket(
        bucket: list[StatuteNameEntry],
        surface: str,
        as_of: Optional[dt.date],
    ) -> RegistryResult:
        """Build a :class:`RegistryResult` from a matched entry bucket.

        Shared by :meth:`lookup` (exact-surface) and
        :meth:`lookup_content_word_set` (content-word fallback): applies the
        as-of filter, collapses to DISTINCT statute ids (one act registered under
        several surfaces counts once), and types the result single / multiple /
        none with the same fail-loud discipline (never picks among >1).
        """
        entries = (
            [e for e in bucket if e.covers(as_of)] if as_of is not None else list(bucket)
        )
        distinct: dict[str, StatuteNameEntry] = {}
        for e in entries:
            distinct.setdefault(e.statute_id, e)
        candidates = tuple(
            Candidate(
                statute_id=e.statute_id,
                canonical_title=e.canonical_title,
                valid_from=e.valid_from,
                valid_to=e.valid_to,
            )
            for e in distinct.values()
        )
        if not candidates:
            status = "none"
        elif len(candidates) == 1:
            status = "single"
        else:
            status = "multiple"
        return RegistryResult(
            registry_status=status,
            candidates=candidates,
            surface=surface,
            as_of=as_of,
        )

    def lookup(
        self,
        name_surface: str,
        as_of: Optional[dt.date] = None,
    ) -> RegistryResult:
        """Resolve a (possibly inflected) statute-name surface.

        ``as_of`` filters candidates to those in force at that instant
        (static-as-of-citing).  ``as_of=None`` resolves against the whole
        timeline (and is allowed to be ``multiple``).
        """
        key = _normalize_key(name_surface)
        bucket = self._index.get(key)
        if not bucket:
            return RegistryResult(registry_status="none", surface=name_surface, as_of=as_of)
        return self._result_from_bucket(bucket, name_surface, as_of)

    def lookup_content_word_set(
        self,
        fi_name_key: str,
        as_of: Optional[dt.date] = None,
    ) -> RegistryResult:
        """FP-gated inflection-robust fallback: match a descriptive cite by content.

        ``fi_name_key`` is the ``fi-name:`` payload the by-name lane mints
        (head-first for descriptive cites: ``laki <body>``). The cite's
        ``(head, content-word-set)`` key (:func:`citation_content_key`) is matched
        against the base-act content index. The match is STRICT:

        * the cite must be a clean head-first ``Laki/Asetus <body>`` shape with a
          clean (non-leak) body carrying >=2 distinctive content stems — otherwise
          ``citation_content_key`` returns ``None`` and this is a ``none`` result
          (a compound nickname / garbage / under-specified complement never enters);
        * the head (laki/asetus) must match the title head (a ``laki`` cite never
          resolves to an ``asetus`` act);
        * the match is on the WHOLE content-word set (an exact set equality via the
          index hash), NOT a loose subset — subset matching is higher-FP and is
          deliberately not done here;
        * >1 candidate is ``multiple`` (fail-loud, never picked), exactly as
          :meth:`lookup`.

        Returns ``status="none"`` when the cite is not a usable content key or no
        base act carries that exact content set. This method is intended to be
        consulted by the resolution projection ONLY after the exact-surface
        :meth:`lookup` has missed.
        """
        content_key = citation_content_key(fi_name_key)
        if content_key is None:
            return RegistryResult(registry_status="none", surface=fi_name_key, as_of=as_of)
        bucket = self._content_index.get(content_key)
        if not bucket:
            return RegistryResult(registry_status="none", surface=fi_name_key, as_of=as_of)
        return self._result_from_bucket(bucket, fi_name_key, as_of)


def build_registry(
    entries: Iterable[
        tuple[str, str, Optional[dt.date], Optional[dt.date]]
        | tuple[str, str]
        | StatuteNameEntry
    ],
    *,
    aliases: Optional[Iterable[StatuteNameAlias]] = STATUTE_NAME_ALIASES,
) -> StatuteNameRegistry:
    """Build a :class:`StatuteNameRegistry` from canonical name->id entries.

    Each entry is either a :class:`StatuteNameEntry`, a 2-tuple
    ``(statute_id, title)``, or a 4-tuple
    ``(statute_id, title, valid_from, valid_to)``.  The title is expanded into
    its inflected surface variants generation-first (see module docstring).

    The curated colloquial-nickname alias table
    (:data:`statute_name_aliases.STATUTE_NAME_ALIASES`) is registered on top by
    default (recall lever for nicknames not derivable from the official title;
    coexists with generated surfaces, fail-loud on cross-id collision).  Pass
    ``aliases=None`` to build a generation-only registry (e.g. to measure the
    alias delta or in a test fixture).
    """
    reg = StatuteNameRegistry()
    for raw in entries:
        reg._register(_coerce_entry(raw))
    if aliases is not None:
        reg.register_aliases(aliases)
    return reg


def _coerce_entry(
    raw: (
        tuple[str, str, Optional[dt.date], Optional[dt.date]]
        | tuple[str, str]
        | StatuteNameEntry
    ),
) -> StatuteNameEntry:
    """Normalize a build_registry input into a :class:`StatuteNameEntry`."""
    if isinstance(raw, StatuteNameEntry):
        return raw
    # A variadic view (indexed, not destructured) so the length-based dispatch
    # type-checks cleanly against the input union.
    fields: list[object] = list(raw)
    if len(fields) == 2:
        return StatuteNameEntry(
            statute_id=str(fields[0]),
            canonical_title=str(fields[1]),
        )
    if len(fields) == 4:
        vf, vt = fields[2], fields[3]
        return StatuteNameEntry(
            statute_id=str(fields[0]),
            canonical_title=str(fields[1]),
            valid_from=vf if isinstance(vf, dt.date) else None,
            valid_to=vt if isinstance(vt, dt.date) else None,
        )
    msg = f"unexpected entry shape: {raw!r}"  # pragma: no cover - defensive
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# Persisted artifact (JSON-lines) — full-corpus registry serialization
# ---------------------------------------------------------------------------
#
# The full ~56k-title registry is a derived DATA ARTIFACT (a pure function of the
# farchive corpus), built by ``lawvm build-statute-name-registry`` and consumed by
# the resolution projection.  It uses the same JSON-lines convention as the other
# Finland derived working files (e.g. ``parse_characterization_golden.jsonl``): a
# leading ``_meta`` header line followed by one entry per line.  The artifact is
# NOT committed (it is regenerable and large, like the ``.farchive`` it derives
# from); only the builder + loader are durable.

_ARTIFACT_KIND = "fi_statute_name_registry_v1"


def _entry_to_jsonable(entry: StatuteNameEntry) -> dict[str, object]:
    """Serialize one entry to a JSON-safe dict (dates as ISO strings or null)."""
    return {
        "statute_id": entry.statute_id,
        "canonical_title": entry.canonical_title,
        "valid_from": entry.valid_from.isoformat() if entry.valid_from else None,
        "valid_to": entry.valid_to.isoformat() if entry.valid_to else None,
    }


def _entry_from_jsonable(obj: dict[str, object]) -> StatuteNameEntry:
    """Inverse of :func:`_entry_to_jsonable`. Never fabricates a window."""

    def _date(v: object) -> Optional[dt.date]:
        if not v:
            return None
        return dt.date.fromisoformat(str(v))

    return StatuteNameEntry(
        statute_id=str(obj["statute_id"]),
        canonical_title=str(obj["canonical_title"]),
        valid_from=_date(obj.get("valid_from")),
        valid_to=_date(obj.get("valid_to")),
    )


def serialize_entries(
    entries: Iterable[StatuteNameEntry],
    path: str | Path,
    *,
    meta: Optional[dict[str, object]] = None,
) -> int:
    """Write ``entries`` to a JSON-lines registry artifact at ``path``.

    The first line is a ``{"_meta": {...}}`` header (``kind`` + any caller-supplied
    counts); each subsequent line is one serialized :class:`StatuteNameEntry`.
    Returns the number of entry rows written.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        header: dict[str, object] = {"kind": _ARTIFACT_KIND}
        if meta:
            header.update(meta)
        f.write(json.dumps({"_meta": header}, ensure_ascii=False) + "\n")
        for entry in entries:
            f.write(
                json.dumps(
                    _entry_to_jsonable(entry), ensure_ascii=False, separators=(",", ":")
                )
                + "\n"
            )
            n += 1
    return n


def load_statute_name_entries(path: str | Path) -> list[StatuteNameEntry]:
    """Read a registry artifact back into a list of :class:`StatuteNameEntry`.

    Skips the ``_meta`` header line. Fail-loud on an artifact of the wrong kind.
    """
    p = Path(path)
    entries: list[StatuteNameEntry] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_meta" in obj:
                kind = obj["_meta"].get("kind")
                if kind != _ARTIFACT_KIND:
                    msg = (
                        f"statute-name registry artifact {p}: unexpected kind "
                        f"{kind!r} (expected {_ARTIFACT_KIND!r})"
                    )
                    raise ValueError(msg)
                continue
            entries.append(_entry_from_jsonable(obj))
    return entries


def load_statute_name_registry(path: str | Path) -> StatuteNameRegistry:
    """Read a persisted artifact at ``path`` into a built :class:`StatuteNameRegistry`.

    The loader resolve.py uses to obtain the full-corpus registry without
    re-enumerating the farchive: it deserializes the entries and runs them back
    through :func:`build_registry` (regenerating the inflected surface variants).
    """
    return build_registry(load_statute_name_entries(path))


def default_artifact_path() -> Path:
    """Canonical path of the persisted full-corpus registry artifact.

    ``$LAWVM_CANONICAL_DATA_ROOT/data/finland/statute_name_registry.jsonl`` (or the
    repo-relative ``data/finland/...`` when the env var is unset).
    """
    import os

    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return Path(root) / "data" / "finland" / "statute_name_registry.jsonl"


def sample_entries_from_farchive(
    limit: int = 500,
    *,
    archive_path: Optional[str] = None,
) -> list[StatuteNameEntry]:
    """Read a SAMPLE of ``(statute_id, title)`` entries from the farchive.

    A convenience data-source for populating the registry; reads at most
    ``limit`` titles so it stays cheap (the full ~56k population is a later
    data-build step, not done here for memory reasons).  Validity windows are
    left open (``None``) --- per-act temporal windows come from the consolidation
    timeline, not the title alone.

    ``archive_path`` defaults to ``$LAWVM_CANONICAL_DATA_ROOT/data/finlex.farchive``.
    """
    import os

    from lxml import etree

    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    if archive_path is None:
        root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
        archive_path = os.path.join(root, "data", "finlex.farchive")

    store = TransparentCorpusStore(Farchive(archive_path, readonly=True))
    out: list[StatuteNameEntry] = []
    for sid in store.list_statute_ids():
        if len(out) >= limit:
            break
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        try:
            tree = etree.fromstring(xb)
        except Exception:
            continue
        title_el = tree.find(".//{*}docTitle")
        if title_el is None:
            continue
        title = " ".join(
            etree.tostring(title_el, method="text", encoding="unicode").split()
        )
        if title:
            out.append(StatuteNameEntry(statute_id=sid, canonical_title=title))
    return out


def _extract_title_and_date(xb: bytes) -> tuple[str, Optional[dt.date]] | None:
    """Extract ``(title, enactment_date|None)`` from one statute XML blob.

    Returns ``None`` when the blob does not parse or carries no ``docTitle``.
    ``enactment_date`` is the ``FRBRWork`` ``dateIssued`` parsed as a date, or
    ``None`` when the corpus lacks/garbles it (NEVER fabricated — see the
    temporal-bounds discipline in ``all_entries_from_farchive``).
    """
    from lxml import etree

    try:
        tree = etree.fromstring(xb)
    except etree.XMLSyntaxError:
        return None
    title_el = tree.find(".//{*}docTitle")
    if title_el is None:
        return None
    title = " ".join(
        etree.tostring(title_el, method="text", encoding="unicode").split()
    )
    if not title:
        return None

    valid_from: Optional[dt.date] = None
    work = tree.find(".//{*}FRBRWork")
    if work is not None:
        for d in work.findall("{*}FRBRdate"):
            if d.get("name") == "dateIssued":
                raw = d.get("date")
                if raw:
                    try:
                        valid_from = dt.date.fromisoformat(raw)
                    except ValueError:
                        valid_from = None
                break
    return title, valid_from


_FINLEX_NS = "http://data.finlex.fi/schema/finlex"


def _extract_repeal_date(oracle_xb: bytes) -> Optional[dt.date]:
    """Extract a statute's REPEAL date from its consolidated oracle XML.

    Finlex publishes the repeal as authoritative consolidation metadata: a
    repealed act's oracle carries a ``finlex:repealedBy`` block whose
    ``finlex:statuteReference / finlex:inForce / finlex:dateEntryIntoForce[@date]``
    is the date the repealing act entered into force --- i.e. the date the
    repealed version stopped being the act of that name (exclusive ``valid_to``).
    This is the deterministic, IN-CORPUS supersession date; no external fetch.

    Returns the parsed date, or ``None`` (fail-loud, never fabricated) when the
    oracle does not parse, carries no ``repealedBy`` block, or that block has no
    parseable ``dateEntryIntoForce`` --- a repealed act whose supersession date
    the corpus does not expose stays an OPEN window, never a guessed date.
    """
    from lxml import etree

    try:
        tree = etree.fromstring(oracle_xb)
    except etree.XMLSyntaxError:
        return None
    repealed_by = tree.find(f".//{{{_FINLEX_NS}}}repealedBy")
    if repealed_by is None:
        return None
    date_el = repealed_by.find(f".//{{{_FINLEX_NS}}}dateEntryIntoForce")
    if date_el is None:
        return None
    raw = date_el.get("date")
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def all_entries_from_farchive(
    *,
    archive_path: Optional[str] = None,
    limit: int = 0,
) -> Iterator[StatuteNameEntry]:
    """STREAM every statute's ``(statute_id, title)`` from the farchive.

    The full-corpus counterpart of :func:`sample_entries_from_farchive`: it walks
    EVERY statute id in the farchive (not a prefix sample) and yields one
    :class:`StatuteNameEntry` at a time.  It reads a single statute's XML blob,
    extracts only the title + enactment date, and DISCARDS the blob before moving
    on, so peak memory stays at one statute's XML regardless of corpus size (the
    WSL2 memory ceiling is real — the corpus is ~60k statutes / multi-GB).

    Temporal bounds (HONEST, never fabricated):

    * ``valid_from`` = the source XML's ``FRBRWork/FRBRdate[@name='dateIssued']``
      (the real enactment date) when present, else ``None`` (open).
    * ``valid_to`` = the REPEAL date read from the statute's consolidated oracle
      (``finlex:repealedBy / ... / dateEntryIntoForce[@date]``, the date the
      repealing act entered into force) when the act has been repealed and the
      corpus exposes that date; else ``None`` (open).  This closes the window of
      a repealed-and-superseded version so as-of-citing disambiguation can drop
      it (an act repealed and re-enacted under the same name).  A still-in-force
      act, or a repealed act whose supersession date the corpus does not expose
      (no oracle / no ``repealedBy`` / unparseable date), keeps an OPEN window —
      the date is never guessed.

    ``limit`` (0 = unbounded) caps the number of ids walked, for cheap tests.
    ``archive_path`` defaults to ``$LAWVM_CANONICAL_DATA_ROOT/data/finlex.farchive``.
    """
    import os

    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    if archive_path is None:
        root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
        archive_path = os.path.join(root, "data", "finlex.farchive")

    store = TransparentCorpusStore(Farchive(archive_path, readonly=True))
    ids = store.list_statute_ids()
    if limit:
        ids = ids[:limit]
    for sid in ids:
        xb = store.read_source(sid) or store.read_amendment(sid)
        if not xb:
            continue
        extracted = _extract_title_and_date(xb)
        del xb  # release the source XML blob immediately (bounded peak memory)
        if extracted is None:
            continue
        title, valid_from = extracted
        # The repeal/supersession date lives ONLY in the consolidated oracle
        # (the source XML carries no repealedBy block); read it separately and
        # discard it immediately to keep peak memory at one blob.
        oracle_xb = store.read_oracle(sid)
        valid_to = _extract_repeal_date(oracle_xb) if oracle_xb else None
        del oracle_xb
        yield StatuteNameEntry(
            statute_id=sid,
            canonical_title=title,
            valid_from=valid_from,
            valid_to=valid_to,
        )


__all__ = [
    "STATUTE_NAME_ALIASES",
    "Candidate",
    "_extract_repeal_date",
    "all_entries_from_farchive",
    "RegistryResult",
    "StatuteNameAlias",
    "StatuteNameEntry",
    "StatuteNameRegistry",
    "build_registry",
    "citation_content_key",
    "content_word_set",
    "default_artifact_path",
    "derive_nicknames",
    "load_statute_name_entries",
    "load_statute_name_registry",
    "sample_entries_from_farchive",
    "serialize_entries",
]
