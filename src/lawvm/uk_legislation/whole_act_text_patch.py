"""UK whole-Act text-patch admission rules.

Whole-Act word patches are dangerous unless the source/effect pair owns
document-wide scope. This module intentionally admits only simple
all-occurrences substitution shapes; listed enactments, exclusions, and
title/short-title carve-outs remain unsupported.
"""

from __future__ import annotations

import re


UK_SIMPLE_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RULE_ID = (
    "uk_effect_simple_whole_act_all_occurrences_substitution_text_patch"
)

_SIMPLE_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RE = re.compile(
    r"""
    \bIn\s+the\s+
    (?P<title>[A-Z][^.;]{1,220}?\bAct\s+(?:1[0-9]{3}|20[0-9]{2}))
    \s+for\s+[“"'](?P<original>[^”"']{1,240})[”"']
    \s+
    (?:
        in\s+each\s+place\s+where\s+(?:those\s+words|that\s+word)\s+occurs?
        |
        wherever\s+(?:it|they|those\s+words|that\s+word)\s+occurs?
    )
    \s+substitute\s+[“"'](?P<replacement>[^”"']{1,240})[”"']
    """,
    flags=re.I | re.S | re.X,
)
_TARGET_CARRIED_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RE = re.compile(
    r"""
    \bfor\s+[“"'](?P<original>[^”"']{1,240})[”"']
    \s+
    (?:
        \(?\s*in\s+each\s+place\s*\)?
        |
        wherever\s+(?:it|they|those\s+words|that\s+word)\s+occurs?
    )
    \s+substitute\s+[“"'](?P<replacement>[^”"']{1,240})[”"']
    """,
    flags=re.I | re.S | re.X,
)

_WHOLE_ACT_EXCLUSION_MARKER_RE = re.compile(
    r"""
    \b
    (?:
        does\s+not\s+apply
        |except(?:\s+in|\s+where|\s+to|\b)
        |other\s+than
        |enactments?\s+listed
        |listed\s+in
        |short\s+title
        |title\s+of\s+any\s+enactment
        |as\s+inserted\s+by
    )
    \b
    """,
    flags=re.I | re.X,
)


def simple_whole_act_all_occurrences_substitution(text: str | None) -> bool:
    """Return True for the bounded whole-Act all-occurrences substitution form."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return False
    if _WHOLE_ACT_EXCLUSION_MARKER_RE.search(normalized):
        return False
    return bool(
        _SIMPLE_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RE.search(normalized)
        or _TARGET_CARRIED_WHOLE_ACT_ALL_OCCURRENCES_SUBSTITUTION_RE.search(normalized)
    )
