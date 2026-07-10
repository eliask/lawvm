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

Presence vs glyph-identity: what "a dash may be substantive" really means
-------------------------------------------------------------------------
``metadata._normalize_fi_parse_text`` folds em-dash→en-dash and Zs→space, but its
docstring is explicit that it MUST NOT be applied to body text compared against oracle
content — those folds are for *parse-text* (extracting section numbers). The distinction
that matters for *payload* text is between a dash's PRESENCE and its GLYPH IDENTITY. The
PRESENCE is substantive: an en-dash range "5—10", an em-dash aside, a compound hyphen
"sotilas- ja" all carry meaning, so a dash must never be DELETED. The GLYPH IDENTITY is
not: which of {hyphen, non-breaking hyphen, figure/en/em dash, horizontal bar, minus
sign} a text layer emits for the same underlying document is a rendering artifact that
changes no word, number, or citation. Quotes are held to the same test but are NOT folded
yet — no residual has convicted a curly/straight quote as the sole inert difference, so
adding it would be speculative. Visible-glyph equivalence is graduated only when residuals
convict it, with the same auditability the Unicode-fold sets carry.

Graduated visible-glyph fold #1: SEPARATOR_DASH_RUN (dash PRESENCE, run only)
----------------------------------------------------------------------------
A RUN of 2+ dashes ("— — —") is a visual rule / statute elision marker, never
substantive content — discovered from residuals (52 HE payload bodies differed ONLY by a
trailing "— — —" the text layer captured but the clean XML omits) and adjudicated inert.
It is DELETED, but a SINGLE dash is deliberately preserved from deletion — the ``{2,}``
requirement is what keeps this inside the "unarguably inert" boundary.

Graduated visible-glyph fold #2: DASH_GLYPH (dash IDENTITY, 1:1 substitution)
----------------------------------------------------------------------------
The dash family (U+2010–2015, U+2212) is normalised to ASCII hyphen-minus U+002D — a 1:1
substitution that KEEPS the dash, so it is orthogonal to SEPARATOR_DASH_RUN (which deletes
runs) and consistent with "a single dash stays substantive" (the dash is still present,
just canonicalised). Discovered from residuals: HE proposed-body ranges that differ ONLY by
the dash glyph the extractor emitted — "1 momentin 1 ― 6 kohdassa" vs "1 - 6", "60―62 §:ssä"
vs "60-62 §:ssä", "9-11 §" vs "9—11 §" — same digits, same range, different dash glyph.
Because the substitution touches ONLY dash codepoints, it can never hide a numeric/citation
difference: "5—10" vs "5—11" stays a residual (the digits still differ). This is the exact
class ``_normalize_fi_parse_text`` treats as inert for parse-text, now convicted inert for
payload comparison too by same-document two-witness residuals.

Graduated fold #3: WHITESPACE_PUNCT (whitespace ADJACENT to punctuation)
-----------------------------------------------------------------------
Whitespace sitting immediately next to a punctuation mark — a space BEFORE ``: ; , . )``
or AFTER ``(`` — is a typesetting artifact, never content. Discovered from residuals and
convicted inert by three independent T1 adjudication runs: ``"9 § :n"`` vs ``"9 §:n"``
(space before ``:n``), ``"( / )"`` vs ``"(/)"`` (spaces inside the parens around a slash),
``"20 ."`` vs ``"20."`` (space before a period), and ``"1 )"`` vs ``"1)"`` (space before a
list-item close paren). Like SEPARATOR_DASH_RUN/DOT_LEADER this is run through ``re.sub``,
but it is strictly WHITESPACE-ONLY: the pattern body matches spaces only, guarded by a
zero-width lookahead/lookbehind on the punctuation glyph, so it NEVER touches or deletes a
digit, a letter, or the punctuation mark itself. Consequently it can never hide a genuine
numeric/citation/word difference ("5,9" vs "5,10", "(768/2005)" vs "(768/2006)",
"veroviraston tai" vs "veroviraston" all stay residuals), and it deliberately folds only
whitespace AROUND punctuation, never a terminal punctuation's PRESENCE ("markkaa." vs
"markkaa" stays a residual — a trailing period can be load-bearing).

``§`` is DELIBERATELY EXCLUDED from the before-set. Unlike ``: ; , . )`` — whose standard
typographic form carries NO leading space, so a leading space is the anomaly to fold — the
standard Finnish section reference ``"N §"`` legitimately CARRIES a space. Folding it would be
wrong-direction normalisation that fires on almost every body (destroying the output-sparse
audit trail) while recovering ZERO additional payload equivalences (measured: 12/12 newly-equal
pairs on a 300-HE sample are §-independent). The one convicted ``§`` residual — a THIN SPACE
U+2009 before ``§`` in ``"2 §:n"`` — is already equalised by the WHITESPACE fold, because
U+2009/U+202F/U+00A0 are all in ``ZS_NON_ASCII_SPACE_CPS`` (Zs→space, then run-collapse); no
``§``-specific handling is needed for it.

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

# A RUN of 2+ dots (a table/TOC LEADER "Käsivarsi....." or an ellipsis), optionally
# space-separated: a visual leader, never substantive content. Discovered from residuals
# (appendix fee-table cells differ only by a dotted leader between the row label and its
# figure). Like the dash run, the ``{2,}``-dot requirement is load-bearing: a SINGLE dot
# stays substantive (a decimal point "2.46", a numbered "10)" list, a version "3.14"), so
# only runs fold. Flat quantifiers (no nested-backtracking risk).
_DOT_LEADER_RE = re.compile(r"\.\s{0,3}\.[\s.]{0,120}")

# The Unicode dash family — HYPHEN U+2010, NON-BREAKING HYPHEN U+2011, FIGURE DASH U+2012,
# EN DASH U+2013, EM DASH U+2014, HORIZONTAL BAR U+2015, MINUS SIGN U+2212 — mapped to the
# ASCII HYPHEN-MINUS U+002D. This canonicalises the *glyph identity* of a dash while keeping
# the dash PRESENT (a 1:1 substitution, never a deletion). See DASH_GLYPH below for why this
# is inert for same-document two-witness comparison. U+00AD SOFT HYPHEN is deliberately NOT
# here — it is a Cf line-join char handled by ``dehyphenate`` / the Cf delete above.
_DASH_GLYPH_TABLE = {cp: 0x2D for cp in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212)}

# Whitespace ADJACENT to punctuation is a typesetting artifact, never content. This
# WHITESPACE-ONLY substitution runs AFTER the Zs→space + run-collapse of the WHITESPACE fold,
# so its input holds only single ASCII spaces. Each branch body matches spaces only and is
# guarded by a ZERO-WIDTH lookahead/lookbehind on the punctuation glyph, so no digit, letter,
# or the punctuation mark itself is ever touched or deleted (a genuine numeric/citation/word
# or terminal-punctuation-PRESENCE difference can never be hidden). See WHITESPACE_PUNCT below.
# NOTE: "§" is deliberately NOT in the before-set. Unlike a colon/period/close-paren (whose
# STANDARD form has no leading space), the standard Finnish section reference "N §" legitimately
# CARRIES a space; deleting it would be wrong-direction normalisation that fires on nearly every
# body (breaking the output-sparse audit trail) while recovering zero additional equivalences on
# the payload. The convicted "thin space before §" residual ("2 §:n") is already equalised by the
# WHITESPACE fold — U+2009/U+202F/U+00A0 are all in ``ZS_NON_ASCII_SPACE_CPS``.
# One alternation, one left-to-right pass: branch 1 = space(s) immediately BEFORE ":;,.)",
# branch 2 = space(s) immediately AFTER "(". A single non-overlapping scan removes both the
# after-"(" and the before-")" spaces of "( / )", collapsing it to "(/)".
_WS_ADJACENT_PUNCT_RE = re.compile(r" +(?=[:;,.)])|(?<=\() +")


class EncodingFold(StrEnum):
    """The closed set of legally-inert folds this module applies to body text.

    Intentionally small: only the invisible/whitespace layer that is inert beyond
    dispute. New members are added ONLY after a residual-driven adjudication confirms a
    visible-glyph class is genuinely inert (the discovery loop in the module docstring).
    """

    SOFT_HYPHEN_JOIN = "soft_hyphen_join"  # dehyphenate: soft-hyphen line break → fused word
    CF_FORMAT = "cf_format"  # invisible Unicode Cf control chars deleted
    WHITESPACE = "whitespace"  # Zs→space + all whitespace runs collapsed + trimmed
    DASH_GLYPH = "dash_glyph"  # dash-family glyph identity normalised to "-" (dash kept, not deleted)
    SEPARATOR_DASH_RUN = "separator_dash_run"  # run of 2+ dashes ("— — —" rule/elision) deleted
    DOT_LEADER = "dot_leader"  # run of 2+ dots (table/TOC leader "....." / ellipsis) deleted
    WHITESPACE_PUNCT = "whitespace_punct"  # space adjacent to punctuation (before ":;,.)" / after "(") removed


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

    # Normalise dash GLYPHS first (a 1:1 substitution that keeps the dash), so the RUN fold
    # then sees a uniform "-" alphabet and a lone surviving dash is a canonical hyphen-minus.
    no_dashglyph = no_cf.translate(_DASH_GLYPH_TABLE)
    if no_dashglyph != no_cf:
        fired.add(EncodingFold.DASH_GLYPH)

    no_run = _SEPARATOR_DASH_RUN_RE.sub(" ", no_dashglyph)
    if no_run != no_dashglyph:
        fired.add(EncodingFold.SEPARATOR_DASH_RUN)

    no_dots = _DOT_LEADER_RE.sub(" ", no_run)
    if no_dots != no_run:
        fired.add(EncodingFold.DOT_LEADER)

    spaced = no_dots.translate(_ZS_TO_SPACE_TABLE)
    collapsed = _WS_RUN.sub(" ", spaced).strip()
    if collapsed != no_dots:
        fired.add(EncodingFold.WHITESPACE)

    # WHITESPACE_PUNCT runs LAST, on the whitespace-normalised form (single ASCII spaces): one
    # non-overlapping pass removes spaces before ":;,.)" and after "(" (so "( / )" → "(/)").
    punct_normed = _WS_ADJACENT_PUNCT_RE.sub("", collapsed)
    if punct_normed != collapsed:
        fired.add(EncodingFold.WHITESPACE_PUNCT)

    return punct_normed, frozenset(fired)


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
