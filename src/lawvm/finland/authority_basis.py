"""Shared authority-basis surface helpers for Finnish delegation cites.

Pure string utilities for the ``nojalla`` authority-basis construction, shared
between the legacy regex extractor (:mod:`lawvm.finland.delegation`) and the
reference-mention lift (:mod:`lawvm.finland.references.ref_mention_extractor`).

This is a neutral leaf module (stdlib only): both the legacy top-level
``delegation`` extractor and the ``references`` package import these helpers
without either depending on the other, removing the previous
``references`` → legacy-``delegation`` backreach.
"""
from __future__ import annotations

from typing import Optional


def _classify_authority_kind(name_word: str) -> str:
    """Classify a ``nojalla`` authority-basis from the act-name word before its id.

    The Finnish inflected name word that immediately precedes the
    ``(NUM/YEAR)`` id carries the drafting kind of the basis:

      - ``lain`` / ``laissa`` / ``laki`` / ``…kaaren`` (codes: maakaari,
        perintökaari, ulosottokaari …)  → ``"act"`` (a laki / statute).
      - ``asetuksen`` / ``asetus``                       → ``"decree"``.
      - ``päätöksen`` / ``päätös``                       → ``"decision"``.

    Returns ``""`` when the word carries no recognizable kind (e.g. the basis
    name is multi-word and the token before the id is a non-name fragment).
    A blank kind is conservative: the lift then keeps the legacy
    non-statutory-instrument typing rather than guessing a statute.
    """
    if not name_word:
        return ""
    w = name_word.lower()
    if 'asetuks' in w or w.endswith('asetus'):
        return 'decree'
    if 'p\xe4\xe4t\xf6ks' in w or w.endswith('p\xe4\xe4t\xf6s'):
        return 'decision'
    # Laki inflections (lain, laissa, laista, lakia, laeista …) and the
    # legislative "code" family (…kaaren / …kaari: maakaari, perintökaari,
    # ulosottokaari) — all statutes.
    if (
        w.endswith('lain')
        or w.endswith('laki')
        or w.endswith('laissa')
        or w.endswith('laista')
        or w.endswith('lakia')
        or w.endswith('laeista')
        or w.endswith('laeissa')
        or w.endswith('kaaren')
        or w.endswith('kaari')
    ):
        return 'act'
    return ''


def _normalize_year(year_str: str, citing_year: Optional[int] = None) -> str:
    """Normalize a 2-digit year string to 4-digit (e.g. '86' → '1986', '04' → '2004').

    A 2-digit ``yy`` is ambiguous between ``19yy`` and ``20yy``. When the CITING
    statute's enactment year is known it is a causal UPPER BOUND on the cited act
    (a decree's authorizing-law cite cannot post-date the decree): pick the ``20yy``
    reading only when it does not post-date the citing year, else ``19yy``.

    Falls back to the legacy fixed cutoff (17-99 → 1917-1999, 00-16 → 2000-2016)
    ONLY when ``citing_year`` is unknown, so callers that cannot supply it keep
    their exact prior behavior.
    """
    if len(year_str) == 4:
        return year_str
    y = int(year_str)
    if citing_year is not None:
        return str(2000 + y) if (2000 + y) <= citing_year else str(1900 + y)
    # Finnish laws: 17-99 → 1917-1999, 00-16 → 2000-2016
    return str(1900 + y) if y >= 17 else str(2000 + y)
