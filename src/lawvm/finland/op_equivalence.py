"""Op equivalence modulo legally-inert encoding — the "what constitutes equality"
relation for PDF→IR op-set comparison.

Two amendment operations reconstructed from the two witnesses of the SAME document
(XML gold vs PDF-reconstructed text) are compared for EXACT equivalence, not fuzzy
similarity. "Exact" is taken modulo a CLOSED set of encoding-only differences that
carry no legal meaning. This module owns that quotient.

Design stance (deliberately minimal — see the discovery loop below)
------------------------------------------------------------------
Hyphenation/whitespace folding is NECESSARY but NOT SUFFICIENT to decide equality.
Rather than speculatively enumerate every possible inert typographic class up front,
this module folds ONLY the classes that are *unarguably* inert for body-text
comparison — invisible format characters, soft-hyphen line joins, and whitespace —
the exact folds LawVM already sanctions elsewhere (``lawvm.core.unicode_folds``,
``ingest.page_elements.dehyphenate``). Everything that survives that fold and still
differs is emitted as a TYPED RESIDUAL carrying both raw forms.

The residual is the product, not a defect to be hidden: residuals-modulo-current-
normalization are the signal that DISCOVERS the next operator. The loop is
``normalize → residual → adjudicate (local-LLM / terminal image) → graduate a
confirmed-inert pattern into a new deterministic fold here``. So the equality relation
grows empirically from what actually survives, never from a spec written in advance.

Why NOT fold visible glyphs (dashes, quotes) here
-------------------------------------------------
``metadata._normalize_fi_parse_text`` folds em-dash→en-dash and Zs→space, but its
docstring is explicit that it MUST NOT be applied to body text compared against oracle
content — those folds are for *parse-text* (extracting section numbers), where a dash
is a range separator, not for *payload text*, where a dash may be substantive. So the
payload quotient here stays strictly inside the invisible/whitespace layer, with ONE
consciously-graduated visible-glyph exception below. Visible-glyph equivalence, if it
turns out to be needed, is discovered from residuals and added consciously — with the
same auditability the Unicode-fold sets already carry.

The one graduated visible-glyph fold: SEPARATOR_DASH_RUN
-------------------------------------------------------
A RUN of 2+ dashes ("— — —") is a visual rule / statute elision marker, never
substantive content — discovered from residuals (52 HE payload bodies differed ONLY by a
trailing "— — —" the text layer captured but the clean XML omits) and adjudicated inert.
It is folded, but a SINGLE dash is deliberately preserved (an en-dash range "5—10", an
em-dash aside, a compound hyphen remain substantive) — the ``{2,}``-dash requirement is
what keeps this inside the "unarguably inert" boundary.

Auditability
------------
Every fold that materially changed a side is recorded on the verdict
(:attr:`TextEquivalence.folds`). A spot-check can then prove no genuine
numeric/citation change hides inside a normalized bucket — the same discipline as the
rest of LawVM's oracle-touch machinery. These quotients are COMPARISON-ONLY and are
never written back into IR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Tuple

from lawvm.core.unicode_folds import CF_FORMAT_CPS, ZS_NON_ASCII_SPACE_CPS
from lawvm.ingest.page_elements import dehyphenate

# Translation tables built once. Cf-format codepoints are DELETED (mapped to None);
# non-ASCII Zs spaces are mapped to an ordinary U+0020 (the whitespace collapse below
# then folds any run to a single space). U+00AD SOFT HYPHEN is a Cf codepoint, but the
# soft-hyphen *line join* (soft-hyphen immediately before a newline) is handled first by
# ``dehyphenate`` so the two word halves fuse rather than leaving a stray space.
_CF_DELETE_TABLE = {cp: None for cp in CF_FORMAT_CPS}
_ZS_TO_SPACE_TABLE = {cp: 0x20 for cp in ZS_NON_ASCII_SPACE_CPS}

_WS_RUN = re.compile(r"\s+")

# A RUN of 2+ dashes (figure/en/em/horizontal-bar U+2012–2015 or hyphen), optionally
# space-separated: a visual RULE / statute elision marker ("— — —"), never substantive
# body content. Discovered from residuals (52 HE payload bodies differed ONLY by a trailing
# "— — —" the text layer captured but the clean XML body omits) and adjudicated inert. The
# ``{2,}``-dash requirement is load-bearing: a SINGLE dash stays substantive (an en-dash
# range "5—10", an em-dash aside, a compound hyphen "sotilas- ja"), so only runs fold.
# Flat quantifiers only (no quantified group → no nested-backtracking risk).
_SEPARATOR_DASH_RUN_RE = re.compile(r"[‒-―\-]\s{0,3}[‒-―\-][\s‒-―\-]{0,120}")


class EncodingFold(StrEnum):
    """The closed set of legally-inert folds this module applies to body text.

    Intentionally small: only the invisible/whitespace layer that is inert beyond
    dispute. New members are added ONLY after a residual-driven adjudication confirms a
    visible-glyph class is genuinely inert (the discovery loop in the module docstring).
    """

    SOFT_HYPHEN_JOIN = "soft_hyphen_join"  # dehyphenate: soft-hyphen line break → fused word
    CF_FORMAT = "cf_format"  # invisible Unicode Cf control chars deleted
    WHITESPACE = "whitespace"  # Zs→space + all whitespace runs collapsed + trimmed
    SEPARATOR_DASH_RUN = "separator_dash_run"  # run of 2+ dashes ("— — —" rule/elision) deleted


def _canonicalize_text(text: str) -> Tuple[str, frozenset[EncodingFold]]:
    """Fold the invisible/whitespace layer; report which classes materially fired.

    Order matters: ``dehyphenate`` first (fuse soft-hyphen line joins BEFORE the Cf
    delete would drop the soft hyphen and leave the halves split by a newline), then
    delete remaining Cf format chars, then normalise all whitespace to single spaces.
    A fold is recorded only if it actually changed the string, so a clean payload
    carries an empty fold set (output-sparse, auditable).
    """
    fired: set[EncodingFold] = set()

    dehyph = dehyphenate(text)
    if dehyph != text:
        fired.add(EncodingFold.SOFT_HYPHEN_JOIN)

    no_cf = dehyph.translate(_CF_DELETE_TABLE)
    if no_cf != dehyph:
        fired.add(EncodingFold.CF_FORMAT)

    no_dash = _SEPARATOR_DASH_RUN_RE.sub(" ", no_cf)
    if no_dash != no_cf:
        fired.add(EncodingFold.SEPARATOR_DASH_RUN)

    spaced = no_dash.translate(_ZS_TO_SPACE_TABLE)
    collapsed = _WS_RUN.sub(" ", spaced).strip()
    if collapsed != no_dash:
        fired.add(EncodingFold.WHITESPACE)

    return collapsed, frozenset(fired)


@dataclass(frozen=True, slots=True)
class TextEquivalence:
    """Verdict of comparing two payload texts modulo the inert-encoding quotient.

    ``equal`` is the exact-after-folding decision. ``folds`` is the audit trail — the
    inert classes that had to fire on either side to reach the canonical forms. When
    ``equal`` is False the two ``*_canon`` strings are the RESIDUAL: the difference that
    survived every inert fold, i.e. the thing to hand to adjudication (local-LLM, then
    terminal image escalation). ``residual`` is the negation of ``equal``, named for the
    call sites that route on "is there something left to adjudicate?".
    """

    equal: bool
    folds: Tuple[EncodingFold, ...]
    left_canon: str
    right_canon: str

    @property
    def residual(self) -> bool:
        return not self.equal


def text_equivalence(left: str, right: str) -> TextEquivalence:
    """Compare two payload texts for exact equality modulo inert encoding.

    Never raises on odd input (empty / None-coerced); a residual is a normal outcome,
    not an error — it is the signal the discovery loop consumes.
    """
    left_canon, lf = _canonicalize_text(left or "")
    right_canon, rf = _canonicalize_text(right or "")
    return TextEquivalence(
        equal=left_canon == right_canon,
        folds=tuple(sorted(lf | rf)),
        left_canon=left_canon,
        right_canon=right_canon,
    )
