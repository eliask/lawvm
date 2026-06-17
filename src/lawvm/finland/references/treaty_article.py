"""Recognizer for treaty (SopS) article references — the ``artikla`` tail.

Closes the residual ``ARTIKLA`` recall tail: article references whose governing
instrument is a **treaty** (cited via the Finnish Treaty Series ``SopS`` or
referred to by the word ``sopimus`` / ``yleissopimus``), not an EU instrument.

Two surface shapes are recognised:

  (a) ``sopimuksen (SopS 20/66) 2 ja 3 artiklassa`` — an explicit ``(SopS NNN/YY)``
      treaty-series id immediately before the article window. The treaty id is
      determinate → ``cite_confidence = EXACT`` with
      ``statute_id = fi:treaty:sops/YYYY/NNN``.

  (b) ``1 §:ssä mainitun sopimuksen 13 artiklassa`` — a treaty referred to by
      word only (``sopimuksen`` / ``yleissopimuksen``), possibly anaphoric
      (``1 §:ssä mainitun sopimuksen``). The instrument is textually a treaty but
      its SopS number is not in scope → ``cite_confidence = STATUTE_ONLY`` with a
      treaty-name placeholder ``fi-treaty-name:sopimus``. We do NOT fabricate a
      SopS number.

Disjointness with :mod:`lawvm.finland.references.eu_directive`
-------------------------------------------------------------
The EU-by-nickname lane fires only when an EU instrument *nickname* (an inflected
``direktiivi`` / ``asetus`` head, or its registry-resolved form) governs the
article. This lane fires only when the governor is a **treaty** (a SopS id or a
``sopimus``-word cue). The two governor classes are mutually exclusive surface
shapes, so the lanes never double-emit on the same article window:

  * a bare ``5 artiklan`` with no treaty/EU governor → BOTH lanes emit nothing;
  * an EU-nickname-governed ``... direktiivin 5 artiklassa`` → only eu_directive;
  * a treaty-governed ``... sopimuksen 5 artiklassa`` → only this lane.

Article coordination reuse
--------------------------
The article number list (``2 ja 3``, ``33—35``) is expanded by the SAME shared
johtolause number-list helper that ``eu_directive`` uses, imported READ-ONLY via
:func:`lawvm.finland.references.eu_directive._expand_articles`. This module owns
no number-list logic of its own.

§1.11 hot-path regex discipline: patterns are compiled at module scope with
bounded quantifiers; a cheap ``"artikla"`` substring guard precedes the matcher.
"""
from __future__ import annotations

import re
from typing import Optional

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)
from lawvm.finland.references.eu_directive import _ARTIKLA_RE, _expand_articles

# ---------------------------------------------------------------------------
# Surface patterns (§1.11: bounded quantifiers, compiled at module scope).
# ---------------------------------------------------------------------------

#: Cheap substring guard the caller checks before running the matcher.
_ARTIKLA_GUARD = "artikla"

# Explicit SopS id, accepting BOTH the 4-digit-year form (``SopS 19/2020``) and
# the legacy 2-digit-year form (``SopS 20/66``). Series number 1-6 digits; year
# either 2 or 4 digits. Bounded throughout.
_SOPS_RE = re.compile(
    r"\bSopS\s+(?P<num>\d{1,6})/(?P<year>\d{2}(?:\d{2})?)\b",
)

# A treaty-by-word cue: an inflected ``sopimus`` / ``yleissopimus`` head. The
# trailing case suffix is permissive; the stem is what marks it as a treaty.
_TREATY_WORD_RE = re.compile(
    r"\b(?:yleis)?sopimu(?:ksen|kseen|ksessa|ksesta|kset|s|sta|sten)\b",
    re.IGNORECASE,
)

# Lookbehind window (chars) from an article phrase to its governing treaty cue.
# Finnish keeps the two adjacent: "<sopimuksen> N ja M artiklassa".
_TREATY_LOOKBEHIND = 80

#: Placeholder statute id for a treaty named only by word (no SopS number).
_TREATY_NAME_PLACEHOLDER = "fi-treaty-name:sopimus"

#: 2-digit-year pivot: ``yy < _YEAR_PIVOT`` → 20yy, else 19yy. The SopS series
#: predates 2000, so a low 2-digit year (e.g. 20) belongs to the 2000s while a
#: high one (e.g. 66) belongs to the 1900s.
_YEAR_PIVOT = 30


def _normalize_sops_year(year: str) -> str:
    """Normalise a SopS year fragment to a 4-digit year.

    A 4-digit fragment is returned verbatim. A 2-digit fragment ``yy`` is
    expanded by the century pivot: ``yy < _YEAR_PIVOT`` → ``20yy`` (e.g.
    ``20`` → ``2020``), else ``19yy`` (e.g. ``66`` → ``1966``).
    """
    if len(year) == 4:
        return year
    yy = int(year)
    century = 2000 if yy < _YEAR_PIVOT else 1900
    return str(century + yy)


def _find_treaty_governor(text: str, before_idx: int) -> Optional[tuple[CiteConfidence, str, str]]:
    """Find the treaty governing an article phrase ending before ``before_idx``.

    Scans the lookbehind window for a treaty governor, preferring the one closest
    to the article phrase. Returns ``(confidence, statute_id, surface)`` where:

      * an explicit ``SopS NNN/YY`` in scope → ``(EXACT, fi:treaty:sops/YYYY/NNN, "SopS …")``;
      * a ``sopimus``-word cue only → ``(STATUTE_ONLY, fi-treaty-name:sopimus, "<cue>")``.

    Returns ``None`` when no treaty governor is present (the article is not a
    treaty reference — owned by other lanes / not ours).
    """
    window_start = max(0, before_idx - _TREATY_LOOKBEHIND)
    window = text[window_start:before_idx]

    # An explicit SopS id is the strongest governor; prefer the closest one.
    best_sops: Optional[re.Match[str]] = None
    for m in _SOPS_RE.finditer(window):
        if best_sops is None or m.start() >= best_sops.start():
            best_sops = m
    if best_sops is not None:
        num = best_sops.group("num")
        year = _normalize_sops_year(best_sops.group("year"))
        return (
            CiteConfidence.EXACT,
            f"fi:treaty:sops/{year}/{num}",
            best_sops.group(0),
        )

    # Otherwise a treaty-by-word cue → STATUTE_ONLY (do not fabricate a number).
    best_word: Optional[re.Match[str]] = None
    for m in _TREATY_WORD_RE.finditer(window):
        if best_word is None or m.start() >= best_word.start():
            best_word = m
    if best_word is not None:
        return (
            CiteConfidence.STATUTE_ONLY,
            _TREATY_NAME_PLACEHOLDER,
            best_word.group(0),
        )

    return None


def recognize_treaty_article_refs(
    text: str,
    *,
    source_statute_id: str = "",
    source_provision_path: str = "",
) -> list[ReferenceMention]:
    """Recognise treaty (SopS) article references in ``text``.

    Returns one :class:`ReferenceMention` per expanded article, in document
    order, each typed ``cite_kind = TREATY``:

      * explicit ``SopS NNN/YY`` governor in scope → ``cite_confidence = EXACT``,
        ``target.statute_id = fi:treaty:sops/YYYY/NNN``, ``section_label`` = article.
      * ``sopimus``-word cue only → ``cite_confidence = STATUTE_ONLY``,
        ``target.statute_id = fi-treaty-name:sopimus``, ``section_label`` = article
        (the SopS number is NOT fabricated).

    An article window with no treaty governor in its lookbehind is skipped — a
    bare ``5 artiklan`` or an EU-nickname-governed ``artikla`` is owned by other
    lanes, not this one (FAIL-LOUD: emit nothing rather than guess a treaty).

    ``source_span`` is ``None`` (document integration re-anchors via
    ``surface_text``); ``source_provision_ref`` is an empty placeholder unless the
    caller supplies the citing context.
    """
    if _ARTIKLA_GUARD not in text:
        return []

    source_ref = ProvisionRef(
        statute_id=source_statute_id,
        provision_path=source_provision_path,
    )
    out: list[ReferenceMention] = []
    for am in _ARTIKLA_RE.finditer(text):
        governor = _find_treaty_governor(text, am.start())
        if governor is None:
            continue
        confidence, statute_id, _surface = governor
        articles = _expand_articles(am.group("nums"))
        if not articles:
            continue
        surface_text = text[am.start() : am.end()].strip()
        for article in articles:
            target = ProvisionRef(statute_id=statute_id, section_label=article)
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target,
                    cite_kind=CiteKind.TREATY,
                    cite_confidence=confidence,
                    phrase_lemma="treaty_article",
                    source_span=None,
                    valid_at_interval=(None, None),
                    edge_subtype=None,
                    surface_text=surface_text,
                )
            )
    return out


__all__ = [
    "recognize_treaty_article_refs",
]
