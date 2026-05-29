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

# Canonical ordered predicate vocabulary.  Each entry pairs the two surface
# forms the call sites need:
#   - regex_fragment: verbose-regex form for replay (whitespace as ``\s+``; the
#     "same meaning" members carry the trailing ``as`` the replay grammar requires);
#   - substring: plain lower-case form for lowering's substring-membership test
#     (no trailing ``as`` — "has the same meaning" is a prefix that still matches
#     "has the same meaning as ...").
# Order matches replay's historical alternation so the built pattern is identical.
_PREDICATES: tuple[tuple[str, str], ...] = (
    ("means", "means"),
    (r"have\s+the\s+same\s+meaning\s+as", "have the same meaning"),
    (r"has\s+the\s+same\s+meaning\s+as", "has the same meaning"),
    (r"have\s+the\s+meaning", "have the meaning"),
    (r"has\s+the\s+meaning", "has the meaning"),
    (r"are\s+to\s+be\s+construed", "are to be construed"),
    (r"is\s+to\s+be\s+construed", "is to be construed"),
    (r"shall\s+be\s+construed", "shall be construed"),
    ("includes", "includes"),
)

_SHALL_BE_CONSTRUED = r"shall\s+be\s+construed"
_INCLUDES = "includes"


def predicate_alternation(*, with_shall: bool = True) -> str:
    """Return the predicate alternation body for interpolation into a regex.

    The returned string reproduces the historical replay constants exactly
    (leading/trailing newline, ``\\n|`` separators) so it can be dropped into the
    existing ``re.X`` patterns byte-for-byte.  ``with_shall=False`` drops the
    ``shall be construed`` member (the ``_WITHOUT_SHALL`` variant).
    """
    fragments = [
        regex for regex, _sub in _PREDICATES if with_shall or regex != _SHALL_BE_CONSTRUED
    ]
    return "\n" + "\n|".join(fragments) + "\n"


def predicate_substrings(*, with_includes: bool = False) -> tuple[str, ...]:
    """Return the plain-text predicate substrings for membership testing.

    ``with_includes=False`` (the lowering default) omits ``includes``, which is
    matched only when a caller explicitly opts in.
    """
    return tuple(
        sub for _regex, sub in _PREDICATES if with_includes or sub != _INCLUDES
    )
