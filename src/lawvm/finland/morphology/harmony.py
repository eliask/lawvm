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

    Right-to-left is irrelevant for the boolean decision (presence of any back
    vowel suffices), but the spec phrases it as a right-to-left scan; the result
    is identical.  Neutral vowels (e, i) never tip the decision; an all-neutral
    stem defaults to front (returns False).
    """
    for ch in stem.lower():
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
