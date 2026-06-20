"""Vowel harmony --- 100% rule, zero data.

Finnish suffix vowels come in archiphoneme pairs (A = a/ae, O = o/oe, U = u/y).
The realization is fixed by the *stem*: if the stem contains any back vowel
(a, o, u) the suffix takes the back variant; otherwise (only front a-umlaut /
o-umlaut / y and/or the neutral vowels e, i) it takes the front variant.  The
neutral vowels e and i do not force harmony, so a stem with only neutral vowels
defaults to front.

This is computed on the *final head* of a compound only; the modifier prefix
does not change the harmony of the inflected tail.
"""

from __future__ import annotations

_BACK_VOWELS = frozenset("aou")
_FRONT_VOWELS = frozenset("äöy")  # a-umlaut, o-umlaut, y


def is_back_harmony(stem: str) -> bool:
    """Return True if ``stem`` selects back-vowel suffixes.

    Harmony is fixed by the **rightmost non-neutral vowel** of the stem: a back
    vowel (a, o, u) -> back suffixes; a front vowel (a-umlaut, o-umlaut, y) ->
    front.  The neutral vowels e, i never tip the decision and are skipped; an
    all-neutral stem defaults to front (returns False).

    Scanning right-to-left (rather than left-to-right and stopping at the first
    non-neutral vowel) is what makes mixed-harmony **compounds** correct: a
    Finnish suffix harmonizes with the FINAL constituent, so ``väliotsikko``
    (front ``väli`` + back ``otsikko``) takes back suffixes (``väliotsikossa``,
    not ``*väliotsikkossä``).  For any simplex word all non-neutral vowels agree,
    so the rightmost equals the leftmost -> behaviour is identical to a single
    presence test.  For an all-neutral final constituent (e.g. ``kaivovesi``)
    the scan reaches an earlier back vowel; this is the documented limitation
    of the surface heuristic (it would wrongly back-harmonize an all-neutral
    final), but bare ``-i`` / ``-e`` finals after a back-vowel modifier are a
    classify-level wall and do not reach generation as compounds here, and the
    all-neutral-only case (no non-neutral vowel anywhere) still defaults front.
    """
    for ch in reversed(stem.lower()):
        if ch in _BACK_VOWELS:
            return True
        if ch in _FRONT_VOWELS:
            return False
    return False


def harmonize(stem: str, archiphoneme: str) -> str:
    """Realize an archiphoneme suffix string against ``stem``.

    Uppercase A/O/U in ``archiphoneme`` are the harmony slots: back -> a/o/u,
    front -> a-umlaut/o-umlaut/y.  All other characters pass through unchanged.
    """
    back = is_back_harmony(stem)
    out: list[str] = []
    for ch in archiphoneme:
        if ch == "A":
            out.append("a" if back else "ä")
        elif ch == "O":
            out.append("o" if back else "ö")
        elif ch == "U":
            out.append("u" if back else "y")
        else:
            out.append(ch)
    return "".join(out)


__all__ = ["harmonize", "is_back_harmony"]
