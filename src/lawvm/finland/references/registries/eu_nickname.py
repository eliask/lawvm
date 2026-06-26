"""EU-instrument nickname -> CELEX registry (deterministic, T2).

Finnish statute prose routinely refers to EU instruments by an established
*nickname* rather than by their CELEX id or full title, e.g.

    teollisuuspäästödirektiivin 33 ja 35 artiklassa
    yleisen tietosuoja-asetuksen 6 artiklan

This module is the deterministic ``eu_nickname -> CELEX`` lookup table required
by §6 of ``notes_internal/FI_REFERENCE_CATALOGUE.md`` for the
``eu.directive_article`` family (T2). It is a *pure* registry: it recognises a
nickname surface and returns the curated CELEX candidate(s); resolution status
(EXACT / AMBIGUOUS / STATUTE_ONLY) is left to the caller via the typed
:class:`RegistryResult`.

Fail-loud contract (§0.3):
  - A nickname that maps to exactly one CELEX -> ``status=single``.
  - A nickname deliberately seeded with >1 CELEX (genuinely ambiguous Finnish
    usage) -> ``status=multiple`` with *all* candidates; the registry NEVER
    silently picks one.
  - An unknown nickname -> ``status=none`` (the caller emits STATUTE_ONLY only
    if it has independent evidence that a directive was named; the registry
    itself just reports "not in table").

Inflection handling (the morphology reuse)
------------------------------------------
Nicknames appear inflected on their *head* morpheme — the modifier prefix is
invariant, the head (``direktiivi`` / ``asetus`` / …) carries the Finnish case
ending: ``teollisuuspäästödirektiivi`` -> ``teollisuuspäästödirektiivin`` /
``…direktiivissä`` / ``…direktiiviä``. We resolve this deterministically by
reusing the merged morphology engine (``lawvm.finland.morphology``):

  1. Each registry lemma is split into ``modifier + known head`` (the head is a
     closed-class statute head — ``direktiivi`` / ``asetus`` / … — verified via
     :func:`lawvm.finland.morphology.is_known_head`).
  2. The engine generates every ``reference_v1`` case form of the *head* via
     :func:`lawvm.finland.morphology.generate_forms`.
  3. The inflected nickname surfaces are the modifier prefix concatenated with
     each generated head form (``teollisuuspäästö`` + {``direktiivi``,
     ``direktiivin``, ``direktiivissä``, …}).

This makes the inflected match a *generated, deterministic* set rather than a
fuzzy suffix heuristic. The precomputed ``inflected surface -> lemma`` map is
built once at import time.

A multi-word nickname (``yleinen tietosuoja-asetus``) is a Finnish *nominative
phrase* whose words agree in case, so its surfaces are the case-synchronized
DIAGONAL — for each grammatical case, every word inflected into THAT case and
joined (``yleisen tietosuoja-asetuksen``) — which is O(words x cases), NOT the
Cartesian product of independent per-word variant sets. The same engine is
reused by ``eu_nickname_binding.build_statute_local_nicknames`` on
ARBITRARY-LENGTH, already-inflected document-derived defined-term aliases; those
are NOT clean nominative phrases, so above a small word bound only the head is
inflected (modifier prefix held at its bound surface). Both forms are bounded —
never the unbounded Cartesian product that explodes (~2e8 strings, OOM) and
fabricates incoherent mixed-case surfaces. See :func:`_inflected_surfaces`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.morphology import (
    classify,
    generate_forms,
    head_entry,
    is_known_head,
)
from lawvm.finland.morphology.api import MorphCase, MorphEntry

# ---------------------------------------------------------------------------
# Curated seed: lemma (nominative) -> tuple of CELEX candidate ids.
#
# A single-element tuple is an unambiguous nickname; a multi-element tuple is a
# genuinely ambiguous Finnish usage (the registry reports ALL candidates and
# refuses to pick). CELEX form: 3<YEAR>{L|R}<NNNN> (L=directive, R=regulation).
# ---------------------------------------------------------------------------

_SEED: dict[str, tuple[str, ...]] = {
    # --- Directives (L) ---
    "teollisuuspäästödirektiivi": ("32010L0075",),  # IED 2010/75/EU
    "vesipuitedirektiivi": ("32000L0060",),  # WFD 2000/60/EY
    "lintudirektiivi": ("32009L0147",),  # Birds 2009/147/EY (codified)
    "luontodirektiivi": ("31992L0043",),  # Habitats 92/43/ETY
    "kaupunkijätevesidirektiivi": ("31991L0271",),  # UWWTD 91/271/ETY
    "ympäristövastuudirektiivi": ("32004L0035",),  # ELD 2004/35/EY
    "palveludirektiivi": ("32006L0123",),  # Services 2006/123/EY
    # --- Regulations (R) ---
    "yleinen tietosuoja-asetus": ("32016R0679",),  # GDPR 2016/679
    "tietosuoja-asetus": ("32016R0679",),  # common short form of GDPR
    "sivutuoteasetus": ("32009R1069",),  # Animal by-products 1069/2009
    "reach-asetus": ("32006R1907",),  # REACH 1907/2006
    "clp-asetus": ("32008R1272",),  # CLP 1272/2008
    "dual-use-asetus": ("32021R0821",),  # Dual-use 2021/821
    "vakavaraisuusasetus": ("32013R0575",),  # CRR — Capital Requirements
    # Regulation (EU) 575/2013 (banking prudential). Unambiguous: the *asetus*
    # form is CRR; the *direktiivi* form (CRD IV / Solvency II) is ambiguous and
    # seeded as MULTIPLE below.
    "kasvinsuojeluaineasetus": ("32009R1107",),  # PPP Reg (EC) 1107/2009
    "biosidiasetus": ("32012R0528",),  # Biocidal Products Reg (EU) 528/2012
    "terveysväiteasetus": ("32006R1924",),  # Nutrition & health claims Reg
    # (EC) 1924/2006 (often "ravinto- ja terveysväiteasetus" in full prose)
    "elintarviketietoasetus": ("32011R1169",),  # Food Information to Consumers
    # Reg (EU) 1169/2011 (FIC)
    # Mined from corpus R4 bindings (support 7 across distinct statutes): the SE
    # Statute, Council Reg (EC) 2157/2001 on the Statute for a European company.
    "eurooppayhtiöasetus": ("32001R2157",),
    # Mined from corpus ``jäljempänä``/parenthetical bindings (CELEX derived from
    # the co-occurring ``(EU) NNNN/YYYY`` corpus id, never guessed). Each binds the
    # nickname to the SAME EU act across many distinct statutes:
    #   ESAP-asetus → corpus id 2023/2859 (support 14): the ESAP Reg (EU) 2023/2859
    "esap-asetus": ("32023R2859",),
    #   kryptovaramarkkina-asetus → corpus id 2023/1114 (support 12, bound as
    #   "EU:n kryptovaramarkkina-asetus"; the EU:n scope qualifier is stripped so
    #   the bare-use surface resolves): MiCA Reg (EU) 2023/1114
    "kryptovaramarkkina-asetus": ("32023R1114",),
    #   markkinoiden väärinkäyttöasetus → corpus id 596/2014 (support 8): MAR
    #   Reg (EU) 596/2014 (number-first ``N:o 596/2014`` → 32014R0596)
    "markkinoiden väärinkäyttöasetus": ("32014R0596",),
    # --- Deliberately ambiguous seed (Finnish usage genuinely splits) ---
    # "jätedirektiivi" is used in prose for both the consolidated Waste
    # Framework Directive (2008/98/EY) and, historically, its predecessor
    # 2006/12/EY. The registry reports both and refuses to pick.
    "jätedirektiivi": ("32008L0098", "32006L0012"),
    # "vesidirektiivi" is NOT a stable term-of-art: Finnish prose prefers the
    # qualified compounds (juomavesidirektiivi 98/83/EY, vesipuitedirektiivi
    # 2000/60/EY). The bare word floats between the Drinking Water Directive
    # 98/83/EY, its recast 2020/2184, and the Water Framework Directive
    # 2000/60/EY. Ambiguous — list all, never pick (was a risky single before).
    "vesidirektiivi": ("31998L0083", "32020L2184", "32000L0060"),
    # "tietosuojadirektiivi" (the *direktiivi*, distinct from the GDPR *asetus*):
    # the old Data Protection Directive 95/46/EY (repealed by GDPR) in pre-2018
    # prose, and the Law Enforcement Directive (EU) 2016/680 ("rikosasioiden
    # tietosuojadirektiivi") in newer prose. Genuinely temporally ambiguous; the
    # GDPR (32016R0679) is NEVER called a directive, so it is not a candidate.
    "tietosuojadirektiivi": ("31995L0046", "32016L0680"),
    # "vakavaraisuusdirektiivi" splits cross-domain: CRD IV 2013/36/EU (banking)
    # vs Solvency II 2009/138/EY (insurance). Resolvable only by sector context,
    # which the registry does not see — list both, refuse to pick.
    "vakavaraisuusdirektiivi": ("32013L0036", "32009L0138"),
    # "maksupalveludirektiivi": PSD1 2007/64/EY vs PSD2 (EU) 2015/2366 (PSD2
    # repealed PSD1). Temporally ambiguous in prose — list both.
    "maksupalveludirektiivi": ("32007L0064", "32015L2366"),
    # "rahoitusvälinedirektiivi" (MiFID): MiFID I 2004/39/EY vs MiFID II (EU)
    # 2014/65/EU (MiFID II repealed MiFID I). Temporally ambiguous — list both.
    "rahoitusvälinedirektiivi": ("32004L0039", "32014L0065"),
    # "energiatehokkuusdirektiivi" (EED): 2012/27/EU vs the recast (EU) 2023/1791
    # (which repealed 2012/27). Temporally ambiguous — list both.
    "energiatehokkuusdirektiivi": ("32012L0027", "32023L1791"),
}

# Heads that the morphology engine knows and that legitimately terminate a
# nickname. (Subset of the closed statute-head class; checked at build time.)
_NICKNAME_HEADS: tuple[str, ...] = ("direktiivi", "asetus")

# A coined nickname is a *nominative phrase* of at most a few words whose words
# agree in case. ``build_statute_local_nicknames`` (eu_nickname_binding.py),
# however, reuses ``_inflected_surfaces`` on ARBITRARY-LENGTH, already-inflected
# document-derived defined-term aliases (e.g. ``tutkimuslääkkeiden hyviä
# tuotantotapoja koskeva delegoitu asetus``), which are NOT clean nominative
# phrases. Above this many words a phrase is treated as such a coined alias: we
# inflect only its head and hold the modifier prefix at its bound surface,
# rather than attempting whole-phrase case agreement (which would both explode
# combinatorially and fabricate incoherent forms for non-nominative fragments).
_MAX_AGREEING_WORDS: int = 3


class RegistryStatus(Enum):
    """Outcome of a nickname lookup."""

    SINGLE = "single"
    """Exactly one CELEX candidate — caller resolves EXACT."""

    MULTIPLE = "multiple"
    """More than one candidate — caller resolves AMBIGUOUS, never picks."""

    NONE = "none"
    """No candidate in the table — caller resolves STATUTE_ONLY (if it has
    independent evidence a directive was named) or treats as unknown."""


@dataclass(frozen=True, slots=True)
class RegistryResult:
    """Result of an :func:`lookup` call.

    Attributes:
        candidates: The CELEX ids that match (length 0 / 1 / >1).
        registry_status: ``single`` / ``multiple`` / ``none`` per :class:`RegistryStatus`.
        lemma:      The matched nickname lemma (nominative), or "" on a miss.
        matched_surface: The surface that triggered the match (possibly inflected).
    """

    candidates: tuple[str, ...]
    registry_status: RegistryStatus
    lemma: str = ""
    matched_surface: str = ""


# ---------------------------------------------------------------------------
# Precomputed inflected-surface -> lemma index (built once at import).
# ---------------------------------------------------------------------------


def _split_head(lemma: str) -> Optional[tuple[str, str]]:
    """Split ``lemma`` into ``(modifier, head)`` if it ends in a known head.

    Returns the longest matching known head suffix, e.g.
    ``teollisuuspäästödirektiivi`` -> ``("teollisuuspäästö", "direktiivi")``.
    ``None`` if no known head terminates the lemma.
    """
    best: Optional[tuple[str, str]] = None
    for head in _NICKNAME_HEADS:
        if lemma.endswith(head) and is_known_head(head):
            modifier = lemma[: -len(head)]
            if best is None or len(head) > len(best[1]):
                best = (modifier, head)
    return best


def _head_case_forms(word: str) -> Optional[dict[MorphCase, set[str]]]:
    """Per-case inflected forms of ``word`` if it ends in a known head.

    Returns ``{case: {surfaces}}`` (modifier prefix + each generated head form,
    keyed by the form's grammatical case) or ``None`` if ``word`` does not end
    in a known morphology head.
    """
    split = _split_head(word)
    if split is None:
        return None
    modifier, head = split
    by_case: dict[MorphCase, set[str]] = {}
    for form in generate_forms(head_entry(head)):
        if form.surface and form.certainty == "deterministic":
            by_case.setdefault(form.case, set()).add(modifier + form.surface)
    return by_case


def _word_case_forms(word: str) -> Optional[dict[MorphCase, set[str]]]:
    """Per-case inflected forms of a single nominative ``word`` (lowercase).

    Returns ``{case: {surfaces}}`` keyed by grammatical case when ``word`` is a
    clean nominative we can confidently inflect — either it ends in a known
    morphology head, or it classifies to a generable paradigm (e.g. a ``-nen``
    adjective like ``yleinen`` -> ``yleisen``). Returns ``None`` when ``word``
    cannot be confidently treated as a nominative to inflect by case; the caller
    then declines the case-synchronized diagonal for the whole phrase.

    Building per-case forms (rather than a flat untyped variant set) lets the
    caller join words on a SHARED case — the linguistically real
    case-agreeing diagonal — instead of the Cartesian product of independent
    per-word variant sets (which is both combinatorially explosive and
    fabricates incoherent mixed-case surfaces that never occur in text).
    """
    head_forms = _head_case_forms(word)
    if head_forms is not None:
        # Always carry the bare nominative under NOM (the head may not emit it).
        head_forms.setdefault(MorphCase.NOM, set()).add(word)
        return head_forms
    cls = classify(word)
    if cls.classification_status != "resolved" or not cls.morph_class:
        return None
    entry = MorphEntry(
        lemma_id=f"nickname-word:{word}",
        lemma=word,
        referent_kind="common",
        morph_class=cls.morph_class,
    )
    by_case: dict[MorphCase, set[str]] = {}
    for form in generate_forms(entry):
        if form.surface and form.certainty == "deterministic":
            by_case.setdefault(form.case, set()).add(form.surface)
    by_case.setdefault(MorphCase.NOM, set()).add(word)
    return by_case


def _head_only_surfaces(words: list[str]) -> set[str]:
    """Inflect only the head (last word); hold the modifier prefix invariant.

    Used for document-derived coined aliases that are NOT clean nominative
    phrases (already-inflected fragments, > ``_MAX_AGREEING_WORDS`` words). The
    case ending of a re-used coined alias lands on its head noun; the modifier
    fragment stays at its bound surface. This is O(cases), never combinatorial,
    and never fabricates incoherent inflections of the modifier fragment.
    """
    prefix = " ".join(words[:-1])
    head_forms = _head_case_forms(words[-1])
    surfaces: set[str] = set()
    if head_forms is None:
        return surfaces
    for forms in head_forms.values():
        for surface in forms:
            surfaces.add(f"{prefix} {surface}" if prefix else surface)
    return surfaces


def _inflected_surfaces(lemma: str) -> set[str]:
    """All inflected surface forms of a (possibly multi-word) ``lemma``.

    For a short clean nominative phrase (the curated seed nicknames, which are
    at most a few words) the words agree in case, so the surfaces that actually
    occur are the case-synchronized DIAGONAL: for each grammatical case, inflect
    every word into THAT case and join. This is O(words x cases) — tens of
    surfaces — NOT the Cartesian product of independent per-word variant sets.
    The product is both explosive (a 6-word document alias reaches ~2e8 strings,
    OOM-ing import/extraction) AND wrong (it fabricates incoherent mixed-case
    combos that never appear in text).

    Above ``_MAX_AGREEING_WORDS`` words — or when any word is not a clean
    nominative we can inflect by case — the phrase is a document-derived coined
    alias, not an agreeing nominative phrase: only the head is inflected and the
    modifier prefix is held at its bound surface (``_head_only_surfaces``).
    Either way the expansion is bounded; there is no unbounded growth.

    Always includes the bare lemma itself. All output is lowercase.
    """
    words = lemma.split()
    if not words:
        return {lemma}

    surfaces: set[str] = {lemma}

    if len(words) <= _MAX_AGREEING_WORDS:
        per_word = [_word_case_forms(w) for w in words]
        if all(forms is not None for forms in per_word):
            # Every word is a clean, case-inflectable nominative.
            word_forms: list[dict[MorphCase, set[str]]] = [
                forms for forms in per_word if forms is not None
            ]
            # Case-synchronized diagonal: join words on a SHARED case. A given
            # word may emit several surfaces for one case (gradation/override);
            # the per-case combination stays tiny (<= a handful per case).
            for case in MorphCase:
                choices = [forms[case] for forms in word_forms if case in forms]
                if len(choices) != len(words):
                    continue  # not every word inflects in this case — skip it
                stack: list[list[str]] = [[]]
                for word_choices in choices:
                    stack = [
                        prefix + [choice]
                        for prefix in stack
                        for choice in sorted(word_choices)
                    ]
                for combo in stack:
                    surfaces.add(" ".join(combo))
            return surfaces

    # Document-derived coined alias (too long, or a non-nominative fragment):
    # inflect the head only, modifier prefix invariant. Bounded by construction.
    surfaces |= _head_only_surfaces(words)
    return surfaces


def _build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for lemma in _SEED:
        for surface in _inflected_surfaces(lemma):
            # First registration wins; on collision keep the longer lemma so a
            # specific nickname (yleinen tietosuoja-asetus) is not shadowed by a
            # shorter one. Collisions across distinct CELEX would be a seed bug.
            existing = index.get(surface)
            if existing is None or len(lemma) > len(existing):
                index[surface] = lemma
    return index


_INFLECTED_INDEX: dict[str, str] = _build_index()


# ---------------------------------------------------------------------------
# Public lookup
# ---------------------------------------------------------------------------


def lookup(nickname_surface: str, as_of: object = None) -> RegistryResult:
    """Resolve a (possibly inflected) nickname surface to CELEX candidate(s).

    Args:
        nickname_surface: The surface as it appears in text, possibly inflected
            on its head (``teollisuuspäästödirektiivin``). Case-insensitive;
            surrounding whitespace is trimmed.
        as_of: Temporal coordinate placeholder. EU instruments do not (yet) need
            a temporal lookup in this curated seed — CELEX ids are stable — so
            this parameter is accepted for interface parity with the
            statute-name registry's ``static-as-of-citing`` convention but is
            currently unused. Reserved for future re-codified-instrument
            disambiguation.

    Returns:
        A :class:`RegistryResult`. ``status`` is ``single`` / ``multiple`` /
        ``none``; on a multi-candidate hit, ALL candidates are returned and the
        caller must not collapse to one (fail-loud, §0.3).
    """
    del as_of  # reserved; see docstring
    key = nickname_surface.strip().lower()
    if not key:
        return RegistryResult(candidates=(), registry_status=RegistryStatus.NONE)

    lemma = _INFLECTED_INDEX.get(key)
    if lemma is None:
        return RegistryResult(candidates=(), registry_status=RegistryStatus.NONE)

    candidates = _SEED[lemma]
    if len(candidates) == 1:
        status = RegistryStatus.SINGLE
    else:
        status = RegistryStatus.MULTIPLE
    return RegistryResult(
        candidates=candidates,
        registry_status=status,
        lemma=lemma,
        matched_surface=nickname_surface.strip(),
    )


def known_lemmas() -> tuple[str, ...]:
    """Return the curated nickname lemmas (nominative), sorted."""
    return tuple(sorted(_SEED))


__all__ = [
    "RegistryResult",
    "RegistryStatus",
    "known_lemmas",
    "lookup",
]
