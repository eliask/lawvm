"""Recognizer for EU directive/regulation references by nickname + article.

Closes the ``eu.directive_article`` family (§2/§3 of the FI Reference
Catalogue), which was 0% captured. It recognises two co-occurring constructs in
Finnish statute prose:

  (a) an EU-instrument **nickname head** (``teollisuuspäästödirektiivin``,
      ``yleisen tietosuoja-asetuksen``) resolved against the deterministic
      ``eu_nickname -> CELEX`` registry; and
  (b) an **article coordination / range** (``33 ja 35 artiklassa``,
      ``12 artiklan``, ``33—35 artiklassa``) parsed with the *shared*
      number-list / range helpers used by the section-reference grammar.

One typed :class:`~lawvm.core.reference_mention.ReferenceMention` is emitted per
expanded article, with ``cite_kind = EU`` and a resolution status:

  * ``EXACT``       — nickname resolved to a single CELEX.
  * ``AMBIGUOUS``   — nickname maps to >1 CELEX (registry refuses to pick).
  * ``STATUTE_ONLY``— a directive/regulation nickname-shaped head was named but
    is not in the registry (the instrument identity is textual; the CELEX is
    pending — tag, don't guess).

Article coordination reuse
--------------------------
The article number list is parsed by tokenising the number fragment with the
johtolause lexer and running the shared recognizers from
``lawvm.finland.johtolause.grammar.sections`` (imported READ-ONLY):

  * :func:`_number_list` — comma/conj/dash list parsing, identical to the one
    the section-reference family uses; it already folds in
  * :func:`_expand_range_single` / the internal ``_expand_range`` — so a written
    range (``33—35``) expands to one entry per article, exactly as section
    ranges do.

This module owns NO number-list logic of its own; it only locates the
``<numbers> artikla<case>`` window and a preceding nickname head, then delegates
the numeric expansion to the shared helpers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.johtolause.grammar.combinators import Cursor
from lawvm.finland.johtolause.grammar.sections import (
    _Scan,
    _number_list,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.references.eu_reference import (
    DIALECT_CROSS_REF,
    recognize_celex,
    recognize_eu_acts,
)
from lawvm.finland.references.lemma_gate import (
    head_plural_external_local_forms,
    head_surface_forms,
)
from lawvm.finland.references.registries import eu_nickname

# ---------------------------------------------------------------------------
# Surface patterns (§1.11: bounded quantifiers, compiled at module scope).
# ---------------------------------------------------------------------------

# A nickname head is a Finnish word ending in an inflected ``direktiivi`` or
# ``asetus`` head (optionally a multi-word phrase with a leading agreeing
# modifier, e.g. ``yleisen tietosuoja-asetuksen``). We capture a small window of
# up-to-two preceding words plus the head word; the registry's morphology-backed
# index does the actual lemma resolution, so this only needs to be permissive
# enough to hand the right surface span to ``eu_nickname.lookup``.
_WORD = r"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö0-9-]*"
# A head word is a single token ENDING in an inflected ``direktiivi`` or
# ``asetus`` form, e.g. ``teollisuuspäästödirektiivin``, ``tietosuoja-asetuksen``.
# The head form is detected by MORPHOLOGY (paradigm inversion) rather than a
# hand-written ``direktiiv|asetu`` suffix-substring guess: ``head_surface_forms``
# returns the full M1-generated paradigm of the EU-instrument heads
# (``direktiivi``, ``asetus``), longest-first, and the token's tail must be one of
# those generated forms.  This is sound (every alternative is a real M1 output of
# a closed head) and kills the consonant-gradation substring bug class
# (``'asetu'`` substring vs the generated gradated ``asetuksen``).  The leading
# compound modifier (``teollisuuspäästö``, ``tietosuoja-``) rides invariant in
# front, exactly as a statute modifier rides before ``laki``; the
# morphology-backed ``eu_nickname.lookup`` then resolves the lemma.
#
# The plural external-local cases (``direktiiveillä``, ``asetuksilla`` …) are
# added via the explicit, sound M1-boundary supplement: M1's reference_v1
# profile cannot emit them (``plural_case_form`` raises), but they are real
# EU-instrument head forms ("näillä direktiiveillä säädetään") the substring
# matcher used to catch, so dropping them would regress coverage.
_EU_HEAD_LEMMAS: tuple[str, ...] = ("direktiivi", "asetus")
_EU_HEAD_FORMS: tuple[str, ...] = head_surface_forms(_EU_HEAD_LEMMAS) + (
    head_plural_external_local_forms(_EU_HEAD_LEMMAS)
)
_EU_HEAD_ALT = "|".join(
    re.escape(f) for f in sorted(set(_EU_HEAD_FORMS), key=lambda s: (-len(s), s))
)
# Optional compound-modifier prefix (any word-stem chars) + a generated head
# form, with a trailing word boundary so the head form is the token tail.
_HEAD_WORD = rf"[A-Za-zÅÄÖåäö0-9-]*(?:{_EU_HEAD_ALT})\b"

# nickname window: optional one or two leading modifier words + the head word.
# Case-sensitive (as the original ``_HEAD_WORD`` was): the generated head forms
# are lowercase, so a lowercase head form is the tail of the token, matching the
# original substring matcher's case sensitivity exactly.
_NICKNAME_RE = re.compile(
    rf"(?:(?P<m2>{_WORD})\s+)?(?:(?P<m1>{_WORD})\s+)?"
    rf"(?P<head>{_HEAD_WORD})",
)

# The article window: a number list (digits with optional letter suffix, joined
# only by explicit list connectors — comma / "ja" / "tai" / dash) immediately
# followed by an inflected ``artikla``.
#
# The number list is built from list ITEMS joined by CONNECTORS, instead of a
# loose ``[\d\s,...]`` class. This stops ``nums`` from reaching back across a
# whitespace-separated standalone number that is NOT part of the list:
# ``2004 8 artiklassa`` no longer captures ``2004`` (the bare ``2004`` is a
# preceding year, not an article), and ``2012 13 ja 14 artiklan`` captures
# ``13 ja 14`` (the real articles) rather than collapsing to ``2012``. Plain
# whitespace between two digit runs is NOT a connector, so the list start anchors
# to the contiguous run.
#
# ReDoS safety (§1.11): every quantifier is bounded and no two adjacent
# unbounded/overlapping repeats exist. An item is ``\d{1,4}`` + optional single
# letter suffix; the list is the item followed by at most a bounded number of
# ``connector item`` pairs; connectors are explicit (no bare ``\s`` bridging two
# digit runs). The whole thing precedes ``artikla`` directly (optional single
# space), so a no-``artikla`` tail fails fast without catastrophic backtracking.
_ARTIKLA_ITEM = r"\d{1,4}(?:\s?[a-z])?"
_ARTIKLA_CONNECTOR = r"(?:\s*[,–—-]\s*|\s+(?:ja|tai|sekä)\s+)"
_ARTIKLA_RE = re.compile(
    rf"(?P<nums>{_ARTIKLA_ITEM}(?:{_ARTIKLA_CONNECTOR}{_ARTIKLA_ITEM}){{0,30}})"
    r"\s*artikla(?:ssa|sta|an|n|a|ksi|lla|lta|lle|t)?\b",
    re.IGNORECASE,
)

# Reasonable lookbehind window (chars) from an article phrase to its governing
# nickname head. Finnish keeps the two adjacent: "<nickname> N ja M artiklassa".
_NICKNAME_LOOKBEHIND = 80

# CELEX sector/type letter per instrument-head stem. A directive is L, a
# regulation is R, a decision is D. The head word that governs the article
# carries the instrument type, so it disambiguates a form-less inline cite
# (e.g. "direktiivin 2009/138/EY" → L → 32009L0138). Most-specific stem first.
_HEAD_CELEX_TYPE: tuple[tuple[str, str], ...] = (
    ("direktiiv", "L"),   # direktiivi → directive
    ("päätöks", "D"),     # päätöksen → decision (gradated stem)
    ("päätös", "D"),
    ("asetuks", "R"),     # asetuksen → regulation (gradated stem)
    ("asetus", "R"),
    ("asetu", "R"),       # broad asetus stem (matches _HEAD_WORD's "asetu")
)


# Year-first slash cite "YEAR/NUMBER/FORM" (e.g. "2009/138/EY", "2001/23/EY").
# The shared NUMBER/YEAR/FORM recognizer requires a 4-digit MIDDLE group, so it
# only reads the number-first order; this picks up the year-first order (4-digit
# year, ≤3-digit act number — unambiguously year-first). Bounded (§1.11).
_YEAR_FIRST_SLASH_CITE = re.compile(
    r"\b(?P<year>\d{4})/(?P<num>\d{1,3})/(?:EU|EY|ETY|EURATOM|ETA)\b",
    re.IGNORECASE,
)


def _celex_type_for_head(head: str) -> Optional[str]:
    """CELEX type letter (L/R/D) implied by an instrument-head surface, or None."""
    low = head.lower()
    for stem, letter in _HEAD_CELEX_TYPE:
        if stem in low:
            return letter
    return None


def _celex_from_formal_cite(window: str, head: str) -> Optional[str]:
    """Resolve an adjacent formal EU cite in ``window`` to a CELEX, or None.

    An EU-by-nickname head with NO registry hit is only resolvable when the same
    window also carries a formal EU cite. Two shapes resolve here:

      * a literal CELEX ("32018R1805") → used verbatim (its own type letter wins);
      * a form-less / formed act cite ("(EU) 2018/1805", "2009/138/EY",
        "(EY) N:o 999/2001") → the (year, number) are taken from the cite and the
        TYPE letter from the governing head word ("direktiivin" → L, "asetuksen"
        → R, "päätöksen" → D), yielding ``3{year}{TYPE}{number:04d}``.

    Returns ``None`` when no formal cite is adjacent (then the bare head is NOT
    emitted — fail-loud, no polluting STATUTE_ONLY double-count).
    """
    # A literal CELEX is self-typing — prefer it (closest one to the article).
    celex_hits = recognize_celex(window, dialect=DIALECT_CROSS_REF)
    if celex_hits:
        return max(celex_hits, key=lambda h: h.start).celex

    # Otherwise an act cite supplies (year, number); the head supplies the type.
    # Collect (start, year, number) from BOTH the shared act recognizer
    # (NUMBER/YEAR/FORM, "(FORM) N:o NUMBER/YEAR", "(FORM) YEAR/NUMBER") and the
    # year-first slash form ("YEAR/NUMBER/FORM") the shared recognizer misses.
    type_letter = _celex_type_for_head(head)
    if type_letter is None:
        return None
    candidates: list[tuple[int, int, int]] = []  # (start, year, number)
    for h in recognize_eu_acts(window, dialect=DIALECT_CROSS_REF):
        try:
            candidates.append((h.start, int(h.year), int(h.number)))
        except ValueError:
            continue
    for m in _YEAR_FIRST_SLASH_CITE.finditer(window):
        candidates.append((m.start(), int(m.group("year")), int(m.group("num"))))
    if not candidates:
        return None
    # The cite closest to the article (largest start offset) governs it.
    _, year, num = max(candidates, key=lambda c: c[0])
    if not (1957 <= year <= 2050):
        return None
    return f"3{year:04d}{type_letter}{num:04d}"


# ---------------------------------------------------------------------------
# Output record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EuDirectiveRef:
    """A single recognised EU directive/regulation article reference.

    Wraps the typed :class:`ReferenceMention` together with the resolution
    bookkeeping a caller (integration step) needs: the matched nickname surface,
    the resolved CELEX (or all candidates when ambiguous), and the article path.
    """

    mention: ReferenceMention
    nickname_surface: str
    celex_candidates: tuple[str, ...]
    article: str

    @property
    def status(self) -> CiteConfidence:
        return self.mention.cite_confidence


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


def _expand_articles(nums_fragment: str) -> list[str]:
    """Expand an article number fragment to one article token per article.

    Delegates entirely to the shared section-grammar number-list recognizer:
    tokenise the fragment, run ``_number_list`` (which already folds in range
    expansion via ``_expand_range``), and project to bare article numbers.
    """
    fragment = nums_fragment.strip().rstrip(",").strip()
    if not fragment:
        return []
    tokens = tokenize(fragment)
    scan = _Scan(Cursor(tokens))
    parsed = _number_list(scan)
    if not parsed:
        return []
    out: list[str] = []
    for num, suffix in parsed:
        out.append(f"{num}{suffix}" if suffix else num)
    return out


def _find_nickname(text: str, before_idx: int) -> Optional[tuple[str, eu_nickname.RegistryResult]]:
    """Find the nickname head governing an article phrase ending before ``before_idx``.

    Scans the lookbehind window for nickname-shaped heads, preferring the one
    closest to the article phrase. Returns ``(surface, RegistryResult)`` — the
    registry result may be ``status=none`` (unknown nickname → STATUTE_ONLY),
    which is still a recognised directive reference, just unresolved.
    """
    window_start = max(0, before_idx - _NICKNAME_LOOKBEHIND)
    window = text[window_start:before_idx]
    best: Optional[tuple[int, str, eu_nickname.RegistryResult]] = None
    for m in _NICKNAME_RE.finditer(window):
        # Try progressively wider surfaces (head only, m1+head, m2+m1+head) so a
        # multi-word nickname (yleisen tietosuoja-asetuksen) resolves while a
        # bare head (teollisuuspäästödirektiivin) also resolves.
        head = m.group("head")
        candidates_surfaces: list[tuple[int, str]] = []
        parts: list[str] = []
        if m.group("m2"):
            parts.append(m.group("m2"))
        if m.group("m1"):
            parts.append(m.group("m1"))
        parts.append(head)
        # widest first, then narrower, then head-only
        for k in range(len(parts)):
            surface = " ".join(parts[k:])
            candidates_surfaces.append((m.start(), surface))
        resolved: Optional[tuple[int, str, eu_nickname.RegistryResult]] = None
        for start, surface in candidates_surfaces:
            res = eu_nickname.lookup(surface)
            if res.status is not eu_nickname.RegistryStatus.NONE:
                resolved = (start, surface, res)
                break
        if resolved is None:
            # No registry hit at any width — but the head is nickname-shaped, so
            # record it as an unknown (STATUTE_ONLY) candidate using the head.
            resolved = (
                m.start("head"),
                head,
                eu_nickname.RegistryResult(
                    candidates=(),
                    status=eu_nickname.RegistryStatus.NONE,
                ),
            )
        # Prefer the match whose head sits closest to the article phrase (largest
        # start offset within the window).
        if best is None or resolved[0] >= best[0]:
            best = resolved
    if best is None:
        return None
    _, surface, res = best
    return surface, res


def _status_for(res: eu_nickname.RegistryResult) -> tuple[CiteConfidence, tuple[str, ...]]:
    """Map a registry result to a (confidence, celex_candidates) pair."""
    if res.status is eu_nickname.RegistryStatus.SINGLE:
        return CiteConfidence.EXACT, res.candidates
    if res.status is eu_nickname.RegistryStatus.MULTIPLE:
        return CiteConfidence.AMBIGUOUS, res.candidates
    return CiteConfidence.STATUTE_ONLY, ()


def recognize_eu_directive_refs(
    text: str,
    *,
    source_statute_id: str = "",
    source_provision_path: str = "",
) -> list[EuDirectiveRef]:
    """Recognise EU directive/regulation article references in ``text``.

    Returns one :class:`EuDirectiveRef` per expanded article. An article window
    with no governing nickname head in its lookbehind is skipped (it is a plain
    same-instrument/section ``artikla`` reference owned by other lanes, not an
    EU-by-nickname reference).

    Args:
        text: The provision body / clause text to scan.
        source_statute_id: Statute the citation lives in (for the source ref).
        source_provision_path: Provision path of the citing text.

    Resolution status per emitted mention:
        EXACT (single CELEX — a registry SINGLE hit, or a bare head resolved via
        an adjacent formal EU cite) / AMBIGUOUS (>1 CELEX, registry MULTIPLE).
        An unresolvable bare head is NOT emitted (see below), so no STATUTE_ONLY
        nickname-only mention is produced.

    Bare-head discipline (FAIL-LOUD): a nickname-shaped head with NO registry hit
    is emitted ONLY when an adjacent formal EU cite is present in the same window
    — then it resolves to that cite's CELEX (EXACT). A head with neither a
    registry hit nor an adjacent formal cite (a domestic ``asetus`` / anaphoric
    ``direktiivin`` whose article number is governed elsewhere) is NOT emitted: a
    bare ``eu-nickname:<head>`` STATUTE_ONLY would be a pure false positive and
    would double-count against the formal-cite lane.
    """
    source_ref = ProvisionRef(
        statute_id=source_statute_id,
        provision_path=source_provision_path,
    )
    out: list[EuDirectiveRef] = []
    for am in _ARTIKLA_RE.finditer(text):
        nickname = _find_nickname(text, am.start())
        if nickname is None:
            continue
        surface, res = nickname
        confidence, celex = _status_for(res)
        if confidence is CiteConfidence.STATUTE_ONLY:
            # No registry hit. Only emit if an adjacent formal EU cite resolves
            # the bare head; otherwise drop it (fail-loud, no polluting
            # STATUTE_ONLY). The window is the nickname lookbehind plus the cite
            # that may sit between the head and the article number.
            window = text[max(0, am.start() - _NICKNAME_LOOKBEHIND) : am.start()]
            resolved_celex = _celex_from_formal_cite(window, surface)
            if resolved_celex is None:
                continue
            confidence = CiteConfidence.EXACT
            celex = (resolved_celex,)
        articles = _expand_articles(am.group("nums"))
        if not articles:
            continue

        # By this point ``confidence`` is EXACT (registry SINGLE or a bare head
        # resolved via an adjacent formal cite) or AMBIGUOUS (registry MULTIPLE).
        # STATUTE_ONLY no longer reaches here: an unresolvable bare head was
        # dropped above (fail-loud), so the article path is never attached to a
        # polluting ``eu-nickname:<head>`` placeholder.
        for article in articles:
            if confidence is CiteConfidence.EXACT:
                target = ProvisionRef(
                    statute_id=f"celex:{celex[0]}",
                    section_label=article,
                )
            else:  # AMBIGUOUS — multiple candidates; do not pick one
                target = ProvisionRef(
                    statute_id="eu-nickname:" + surface,
                    section_label=article,
                )
            mention = ReferenceMention(
                source_provision_ref=source_ref,
                target_provision_ref=target,
                cite_kind=CiteKind.EU,
                cite_confidence=confidence,
                phrase_lemma="eu_directive_nickname_article",
                source_span=None,
                valid_at_interval=(None, None),
                edge_subtype=None,
                surface_text=text[am.start() : am.end()].strip(),
            )
            out.append(
                EuDirectiveRef(
                    mention=mention,
                    nickname_surface=surface,
                    celex_candidates=celex,
                    article=article,
                )
            )
    return out


__all__ = [
    "EuDirectiveRef",
    "recognize_eu_directive_refs",
]
