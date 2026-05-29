"""Canonical UK statutory-definition grammar vocabulary.

A UK definition entry has the shape::

    "term" <predicate> <body> ;

where ``<predicate>`` is one of a small, closed vocabulary of drafting verbs
("means", "includes", "has the meaning", "is to be construed", ...).

That predicate vocabulary was historically encoded three times with subtly
different membership — once as a regex alternation in replay
(``replay_text_apply._UK_DEFINITION_PREDICATE_PATTERN``) and twice in lowering
(``source_definition_fragments._SOURCE_DEFINITION_ENTRY_PREDICATES`` plus an
inline regex).  This module is the single source of truth.

Two surface forms are provided because the two call sites match differently:
- replay matches with a verbose regex over live text → ``predicate_alternation``;
- lowering tests whether a predicate appears as a substring in an instruction
  tail → ``PREDICATE_SUBSTRINGS``.

Pure stdlib, no replay/XML imports (extraction-ready, same discipline as
``core/regex_safety.py``).
"""

from __future__ import annotations

# Canonical ordered predicate vocabulary, as regex fragments (whitespace as
# ``\s+``).  Order is preserved so a built alternation is stable.  This is the
# replay superset; ``shall be construed`` is the only member some sites drop.
_PREDICATE_FRAGMENTS: tuple[str, ...] = (
    "means",
    r"have\s+the\s+same\s+meaning\s+as",
    r"has\s+the\s+same\s+meaning\s+as",
    r"have\s+the\s+meaning",
    r"has\s+the\s+meaning",
    r"are\s+to\s+be\s+construed",
    r"is\s+to\s+be\s+construed",
    r"shall\s+be\s+construed",
    "includes",
)

_SHALL_BE_CONSTRUED = r"shall\s+be\s+construed"


def predicate_alternation(*, with_shall: bool = True) -> str:
    """Return the predicate alternation body for interpolation into a regex.

    The returned string reproduces the historical replay constants exactly
    (leading/trailing newline, ``\\n|`` separators) so it can be dropped into the
    existing ``re.X`` patterns byte-for-byte.  ``with_shall=False`` drops the
    ``shall be construed`` member (the ``_WITHOUT_SHALL`` variant).
    """
    fragments = [
        f for f in _PREDICATE_FRAGMENTS if with_shall or f != _SHALL_BE_CONSTRUED
    ]
    return "\n" + "\n|".join(fragments) + "\n"
