"""Surface -> morph_class assignment, fail-loud on the genuine walls.

``classify`` returns a :class:`Classification` whose ``classification_status`` is one of:

* ``resolved``   --- a single categorical rule fired; ``morph_class`` is set.
* ``ambiguous``  --- several plausible classes; ALL are listed in
  ``candidates``, none is silently picked.
* ``needs_flag`` --- the surface is in a known wall (the ``-Us`` adj/verb split,
  bare ``-i`` simplexes) that cannot be resolved without head-class info.
* ``unsupported``--- no rule applies.

The two non-negotiable walls (spec): the ``-Us`` adjective-vs-verb split
(``oikeus -> -Ude-`` vs ``asetus -> -Ukse-``) and bare ``-i`` finals
(``tuoli``/``pankki``/``vesi``).  Both -> typed status, never a ranked guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ClassificationStatus = Literal["resolved", "ambiguous", "needs_flag", "unsupported"]


@dataclass(frozen=True, slots=True)
class Classification:
    """Result of classifying a surface into a morph_class."""

    classification_status: ClassificationStatus
    morph_class: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""
    extra_flags: dict[str, object] = field(default_factory=dict)


def classify(surface: str) -> Classification:
    """Classify ``surface`` (a nominative lemma) into a morph_class."""
    s = surface.lower()

    # -nen -> Kotus 38.
    if s.endswith("nen"):
        return Classification(classification_status="resolved", morph_class="-nen")

    # -Uus / -ous quality abstracts: oikeus, vapaus, mahdollisuus -> -Ude-.
    # These are the -us TRAP's resolvable side ONLY when the -uu-/-Vu- shape
    # disambiguates; a bare -us after a consonant is the wall (see below).
    if s.endswith(("uus", "yys")):
        return Classification(classification_status="resolved", morph_class="-Uus->-Ude-")

    # -sto / -sto (collective) and -io / -io: plain vowel-final, no gradation.
    if s.endswith(("sto", "stö", "io", "iö")):
        return Classification(classification_status="resolved", morph_class="vowel_final")

    # -Us / -Os after a consonant or short vowel is THE WALL: deverbal -Ukse-
    # (asetus) vs quality -Ude- (oikeus) cannot be told apart from the surface.
    if s.endswith(("us", "ys", "os", "ös")):
        return Classification(
            classification_status="needs_flag",
            candidates=("-Us->-Ukse-", "-Uus->-Ude-"),
            reason=(
                "the -Us/-Os ending is the deverbal/quality wall; head-class or "
                "an explicit gradation flag is required (asetus->-Ukse- vs "
                "oikeus->-Ude-)"
            ),
        )

    # Bare -i finals are a wall: stable loan (direktiivi, tuoli), gradating
    # (pankki), or old -i/-e (vesi) -> all plausible, never a silent pick.
    if s.endswith("i"):
        return Classification(
            classification_status="ambiguous",
            candidates=("vowel_final", "e_contract"),
            reason=(
                "bare -i simplex: loan-stable vs gradating vs historic -te "
                "i-stem cannot be resolved from the surface alone"
            ),
        )

    # -e finals: contracted -ee (Tampere/-e nouns).
    if s.endswith("e"):
        return Classification(classification_status="resolved", morph_class="e_contract")

    # Other vowel-final lemmas (-o/-u/-y/-a/-ä/-ö) are plain vowel_final, but
    # gradation occurrence still needs a flag if a gradating cluster is present.
    if s and s[-1] in "aäoöuy":
        return Classification(classification_status="resolved", morph_class="vowel_final")

    return Classification(
        classification_status="unsupported",
        reason=f"no categorical rule for ending of {surface!r}",
    )


__all__ = ["Classification", "classify"]
