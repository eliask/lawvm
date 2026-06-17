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
# A head word is a single token CONTAINING the inflected stem of ``direktiivi``
# or ``asetus`` (``direktiiv`` / ``asetu``), e.g. ``teollisuuspäästödirektiivin``,
# ``tietosuoja-asetuksen``.
_HEAD_WORD = r"[A-Za-zÅÄÖåäö0-9-]*(?:direktiiv|asetu)[A-Za-zÅÄÖåäö0-9-]*"

# nickname window: optional one or two leading modifier words + the head word.
_NICKNAME_RE = re.compile(
    rf"(?:(?P<m2>{_WORD})\s+)?(?:(?P<m1>{_WORD})\s+)?"
    rf"(?P<head>{_HEAD_WORD})",
)

# The article window: a number list (digits, commas, conjunctions, dashes,
# letter suffixes) immediately followed by an inflected ``artikla``.
_ARTIKLA_RE = re.compile(
    r"(?P<nums>\d[\d\s,a-z–—-]*?)\s*"
    r"artikla(?:ssa|sta|an|n|a|ksi|lla|lta|lle|t)?\b",
    re.IGNORECASE,
)

# Reasonable lookbehind window (chars) from an article phrase to its governing
# nickname head. Finnish keeps the two adjacent: "<nickname> N ja M artiklassa".
_NICKNAME_LOOKBEHIND = 80


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
        EXACT (single CELEX) / AMBIGUOUS (>1 CELEX) / STATUTE_ONLY (nickname
        named but unknown to the registry).
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
        articles = _expand_articles(am.group("nums"))
        if not articles:
            continue

        for article in articles:
            if confidence is CiteConfidence.STATUTE_ONLY:
                # Instrument identity is textual but CELEX is pending. The act is
                # "known" only as a nickname surface; carry it in statute_id so
                # the article path is not silently dropped.
                target = ProvisionRef(
                    statute_id=f"eu-nickname:{surface}",
                    section_label=article,
                )
            elif confidence is CiteConfidence.EXACT:
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
