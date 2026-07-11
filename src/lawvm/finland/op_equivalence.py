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

Why NOT fold a dash's glyph identity (en-dash vs em-dash) here
-------------------------------------------------------------
``metadata._normalize_fi_parse_text`` folds em-dash→en-dash and Zs→space, but its
docstring is explicit that it MUST NOT be applied to body text compared against oracle
content — those folds are for *parse-text* (extracting section numbers), where a dash is
a range separator, not for *payload* text, where a dash may be substantive. It is TEMPTING
to argue the GLYPH IDENTITY of a single dash (which of {hyphen, non-breaking hyphen,
figure/en/em dash, horizontal bar, minus sign} the text layer emitted) is a mere rendering
artifact and fold it to a canonical hyphen-minus. That is deliberately NOT done: a
speculative always-on single-dash glyph fold is exactly the "exactness, not slop" failure
this quotient exists to prevent — it silently declares "16 a–b" and "16 a—b" EQUAL, hiding
a VISIBLE difference that only the discovery loop (T1 adjudication → image tier) is
entitled to judge inert. A single dash's presence AND glyph therefore stay substantive: a
visible dash difference SURVIVES as a typed residual, never folded to equal. (Quotes are
held to the same test and are likewise NOT folded — no residual has convicted a
curly/straight quote as inert either.) The ONE dash fold that graduates is a run DELETION,
below, which cannot touch a lone substantive dash.

Graduated visible-glyph fold: SEPARATOR_DASH_RUN (dash PRESENCE, run only)
-------------------------------------------------------------------------
A RUN of 2+ dashes ("— — —") is a visual rule / statute elision marker, never
substantive content — discovered from residuals (52 HE payload bodies differed ONLY by a
trailing "— — —" the text layer captured but the clean XML omits) and adjudicated inert.
It is DELETED, but a SINGLE dash is deliberately preserved from deletion — the ``{2,}``
requirement is what keeps this inside the "unarguably inert" boundary. A lone dash keeps
BOTH its presence and its glyph, so an en-dash-vs-em-dash difference on a single dash still
falls through as a residual (see above).

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

Graduated fold #4: CONTROL_STRIP (C0/C1 control-char noise from broken CMaps)
----------------------------------------------------------------------------
A broken ToUnicode CMap (the dominant appendix-table failure mode) does not only mis-map
visible glyphs — it also emits stray C0/C1 CONTROL characters (Unicode category ``Cc``:
U+0000–U+001F, U+007F–U+009F, minus the TAB/LF/VT/FF/CR whitespace controls the WHITESPACE
fold already owns) interspersed in the text layer. Those control bytes carry no lexical
meaning in a statute — they are the EXACT same "invisible noise, unarguably inert" class as
the Cf format chars, just in the sibling ``Cc`` category — so they are DELETED. ``Cc`` is a
FIXED, closed codepoint range that never changes across Unicode versions, so no drift-guard
literal is needed. Because the substitution deletes only control codepoints, it can never
hide a digit/letter/citation difference (those all survive).

Why NOT a unicode-confusable substitution
-----------------------------------------
The same broken CMaps also map ``ä``→``‰``, ``ö``→a control char, ``§``→``ß`` — a *visible-
glyph* corruption. It is TEMPTING to add a confusable→canonical substitution to rescue those
cells, but that is deliberately NOT done here: a confusable fold is NOT inert — mapping
``ß``→``§`` (or any visible glyph to another) could hide a genuine content difference, exactly
the failure this quotient exists to prevent. That corrupt-font stratum is instead routed to a
vision second-witness (``fi_appendix_structure.table_escalation_route`` →
``vision_escalate``), never folded away. Only the *invisible* control-char noise (CONTROL_STRIP)
graduates; the *visible* corruption stays a residual for adjudication.

Graduated fold #5: WHITESPACE_MIDWORD (pdfium errant mid-word space, letter-letter only)
----------------------------------------------------------------------------------------
The pdfium text layer of an HE bill mis-emits a stray SPACE inside a single word — a
kerning artifact where the glyph-advance between two letters is read as a word break, so
``alueeseen`` extracts as ``alue eseen`` and (the dominant form) a leading capital detaches
as ``Verotuksen`` → ``V erotuksen``. The inverse also occurs: an under-space MERGES two words
(``tai siihen`` → ``taisiihen``). Both are the SAME artifact — the reader mis-placed a
word boundary between two letters — and both change no letter, digit, or citation, only where
the space sits. Convicted inert by the T1 adjudicator guard: over 582 labelled residual
snippet pairs, applying this fold flips ZERO of the 137 ``GENUINE_DIFFERENCE``, ZERO of the
148 ``ORACLE_ARTIFACT`` and ZERO of the 213 ``SEGMENTATION_NOISE`` pairs to equal; the single
``READER_DEFECT`` it folds is itself a pure spacing merge (``tai siihen`` ≡ ``taisiihen``),
i.e. a spacing artifact the adjudicator bucketed conservatively, not a garbled word.

The rule is the NARROWEST capture of the artifact: two texts are equal if they are equal after
deleting a space that sits STRICTLY BETWEEN TWO UNICODE LETTERS (a word-char that is neither a
digit nor an underscore — ``[^\\W\\d_]`` — on both flanks),
applied SYMMETRICALLY to both sides. It is deliberately NOT a blanket "remove all spaces":
- a DIGIT flank is untouched, so a thousands separator ``2 500`` never folds to ``2500`` (the
  seam is digit-space-digit) and a ``§``/section number ``5 §`` / ``4 a`` is never disturbed;
- a PUNCTUATION flank is untouched, so ``markkaa .`` / ``( / )`` spacing is left to WHITESPACE_PUNCT.
Because it deletes ONLY a letter-letter space, it can never hide a numeric, citation, or glyph
difference — those all survive into the residual ("5—10" vs "5—11", "(768/2005)" vs "(768/2006)"
stay divergent). The ONE class it CAN in principle collapse is a genuine word-boundary difference
among letters (``työn antaja`` vs ``työnantaja``): this is an accepted, DOCUMENTED trade — such a
pair means the SAME letters in the SAME order and the adjudicator guard finds none in the genuine-
difference corpus, so in practice it masks no real amendment. Applied output-sparsely: the fold is
attempted ONLY when the two texts still differ after every other fold, and recorded ONLY when it
actually rescues equality, so a clean or otherwise-decided pair carries no WHITESPACE_MIDWORD tag
(preserving the output-sparse audit trail — the same discipline that keeps ``§`` out of the punct set).

Graduated fold #6: WHITESPACE_SEP (whitespace adjacent to a range/citation separator)
-------------------------------------------------------------------------------------
The dominant residual *sub-cause* of ``payload_mismatch`` on the wide HE sweep is a pure
SPACING difference where the PDF text layer over- or under-spaces a NUMERIC-CONTEXT
separator that WHITESPACE_PUNCT does not reach. Characterised on the 300-HE sweep (seed 0):
of the 13 pure-spacing residual fragments, 12 sit adjacent to a *separator* glyph and one
sits strictly between two letters (that last is a genuine pdfium word-split, left to the
output-sparse WHITESPACE_MIDWORD fallback — see below). The 12 separator seams are, by
frequency: a space next to a numeric-range DASH ("195— 196", "2014– 2020", 10 of 12), a
space inside a citation SLASH ("37 /1895"), a comma inside a section-number ENUMERATION
("169, 209"), a clitic COLON after a §/number ("13 §: ssä"), and a list-marker PAREN
("3) kohta"). Each is a space touching a NON-alphanumeric separator whose glyph is a
range/citation/enumeration mark, never a compound-word joiner — so the space around it is
typographic, never content. The fold DELETES only that space (the separator glyph itself is
untouched), applied SYMMETRICALLY to both witnesses, so — exactly like WHITESPACE_PUNCT — it
cannot hide a numeric/citation/glyph/word difference: the non-space skeleton is preserved, so
any real digit/letter/dash-glyph change survives as a residual ("195—196" vs "195—197",
"2014–2020" vs "2015–2020", an en-dash vs em-dash on a lone dash all stay divergent).

The separator set is DELIBERATELY NARROW and data-derived:
- the four TYPOGRAPHIC dashes U+2012–2015 (``‒ – — ―``) and ``/`` — range/citation marks —
  have their adjacent space folded on EITHER side. The ASCII HYPHEN ``-`` is EXCLUDED: it is
  a compound-word joiner ("sotilas- ja siviilihenkilöstö"), where the space after it is a
  legitimate word boundary, not a typesetting artifact; folding it would be wrong.
- a ``,`` is folded ONLY as ``digit , SPACE digit`` (a section-number enumeration "169, 209"),
  never a prose comma ("virasto, joka") — the digit-both-flanks guard keeps ordinary prose
  spacing out of the fold (preserving the output-sparse audit trail);
- a ``:`` is folded ONLY as a clitic colon after a ``§`` or digit ("§: ssä", "10: nnessä"),
  never a prose colon ("otsikko: teksti");
- a ``)`` is folded ONLY as a list-marker after a digit ("3) kohta"), never a prose paren.

``§`` is DELIBERATELY EXCLUDED here too (as it is from WHITESPACE_PUNCT): the standard "N §"
reference legitimately carries a space, so folding it would fire on almost every body while
recovering ZERO net additional HE equivalences (the one §-adjacent residual co-occurs with a
genuine letter-letter word-split in HE 266/2002, which MUST stay a divergence). That §
exclusion is load-bearing for correctness: HE 266/2002's payload differs by (a) a comma-
enumeration space (folded here), (b) a space before ``§`` (NOT folded), and (c) the word-split
"säädetään"→"sääde tään". Because the § seam is left intact, the two witnesses still differ
after this fold, so the output-sparse WHITESPACE_MIDWORD fallback — which would otherwise
delete the letter-letter split and wrongly declare the bodies equal — never fires; the genuine
word-split correctly SURVIVES as a residual. A mutation test pins this (HE 266/2002 divergent).

Digit-LETTER spacing ("5 a" vs "5a") is NOT folded — no such seam appears in the sweep, and
the pre-existing WHITESPACE_MIDWORD contract already pins "4 a" vs "4a" (a section sub-label)
as a residual; folding it would regress that guard.

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

# C0/C1 CONTROL characters (Unicode general category Cc) EXCEPT the whitespace controls
# (TAB U+0009, LF U+000A, VT U+000B, FF U+000C, CR U+000D) that the WHITESPACE fold below
# legitimately normalises. Cc is a FIXED, closed range (U+0000–U+001F, U+007F–U+009F) that
# never changes across Unicode versions, so it is written inline — no drift-guard literal is
# needed. Broken ToUnicode CMaps spray these as stray noise bytes into the text layer; they
# carry no lexical meaning, so they are DELETED (the same inert class as the Cf format chars).
_WHITESPACE_CONTROL_CPS = frozenset({0x09, 0x0A, 0x0B, 0x0C, 0x0D})
_CC_CONTROL_DELETE_TABLE = {
    cp: None
    for cp in [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)]
    if cp not in _WHITESPACE_CONTROL_CPS
}

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

# A single SPACE sitting strictly between two Unicode LETTERS — the pdfium errant mid-word
# space (kerning artifact): "alue eseen" ← "alueeseen", "V erotuksen" ← "Verotuksen", and the
# inverse merge "taisiihen" ← "tai siihen". ``[^\W\d_]`` matches a word char that is NOT a digit
# and NOT underscore, i.e. a letter (Unicode-aware for str). The zero-width lookbehind/lookahead
# mean ONLY the space is deleted and ONLY when BOTH flanks are letters: a digit flank (thousands
# separator "2 500", section "5 §" / "4 a") or a punctuation flank is never touched. Runs of
# single spaces are each matched independently in one left-to-right pass ("a b c" → "abc"). Flat
# zero-width guards → no nested-backtracking risk. Applied as an output-sparse fallback (see
# ``text_equivalence``), so it is recorded only when it actually rescues an equality.
_MIDWORD_SPACE_RE = re.compile(r"(?<=[^\W\d_]) (?=[^\W\d_])")

# Whitespace adjacent to a RANGE/CITATION SEPARATOR — the second pure-spacing quotient, a
# strict SUPERSET of the seams WHITESPACE_PUNCT leaves behind. Data-derived from the 300-HE
# sweep (see the WHITESPACE_SEP docstring section): a single ASCII space (its input is the
# whitespace-normalised form) is deleted when it touches one of the NON-alphanumeric separators
# below. Runs AFTER WHITESPACE_PUNCT, one non-overlapping left-to-right pass, flat zero-width
# guards only (no quantified group → no nested-backtracking). It NEVER touches a separator
# glyph, a digit, or a letter — only the space — so the non-space skeleton is preserved and no
# numeric/citation/glyph/word difference can hide. ``§`` and the ASCII hyphen ``-`` are
# DELIBERATELY EXCLUDED (a standard "N §" reference and a compound-word "sotilas- ja" both carry
# a legitimate space); the comma/colon/paren branches are digit/§-anchored so ordinary PROSE
# spacing ("virasto, joka", "otsikko: teksti") never folds — preserving the output-sparse trail.
_WS_SEP_RE = re.compile(
    r"(?<=[/‒-―]) "         # space immediately AFTER a slash or typographic dash (U+2012–2015)
    r"| (?=[/‒-―])"         # space immediately BEFORE a slash or typographic dash
    r"|(?<=\d,) (?=\d)"     # space in a section-number ENUMERATION: "169, 209"
    r"|(?<=[§\d]:) (?=\w)"  # space after a CLITIC COLON (colon following §/digit): "13 §: ssä"
    r"|(?<=\d\)) (?=\w)"    # space after a LIST-MARKER paren: "3) kohta"
)


class EncodingFold(StrEnum):
    """The closed set of legally-inert folds this module applies to body text.

    Intentionally small: only the invisible/whitespace layer that is inert beyond
    dispute. New members are added ONLY after a residual-driven adjudication confirms a
    visible-glyph class is genuinely inert (the discovery loop in the module docstring).
    """

    SOFT_HYPHEN_JOIN = "soft_hyphen_join"  # dehyphenate: soft-hyphen line break → fused word
    CF_FORMAT = "cf_format"  # invisible Unicode Cf control chars deleted
    CONTROL_STRIP = "control_strip"  # C0/C1 Cc control-char noise (broken CMap) deleted
    WHITESPACE = "whitespace"  # Zs→space + all whitespace runs collapsed + trimmed
    SEPARATOR_DASH_RUN = "separator_dash_run"  # run of 2+ dashes ("— — —" rule/elision) deleted
    DOT_LEADER = "dot_leader"  # run of 2+ dots (table/TOC leader "....." / ellipsis) deleted
    WHITESPACE_PUNCT = "whitespace_punct"  # space adjacent to punctuation (before ":;,.)" / after "(") removed
    WHITESPACE_MIDWORD = "whitespace_midword"  # pdfium errant space strictly between two letters removed
    WHITESPACE_SEP = "whitespace_sep"  # space adjacent to a range/citation separator (dash "/" enum-comma clitic-colon list-paren) removed


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

    # Delete C0/C1 control-char noise (the broken-CMap sibling of the Cf delete). Runs after
    # dehyphenate (which owns the LF-bearing line joins) so only the non-whitespace controls,
    # i.e. the pure noise bytes, are removed here.
    no_ctrl = no_cf.translate(_CC_CONTROL_DELETE_TABLE)
    if no_ctrl != no_cf:
        fired.add(EncodingFold.CONTROL_STRIP)

    # Delete RUNS of 2+ dashes (visual rule / "— — —" elision marker). A SINGLE dash is
    # deliberately left untouched — its presence AND glyph identity stay substantive, so an
    # en-dash-vs-em-dash difference on a lone dash falls through as a residual (not folded).
    no_run = _SEPARATOR_DASH_RUN_RE.sub(" ", no_ctrl)
    if no_run != no_ctrl:
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

    # WHITESPACE_SEP runs after WHITESPACE_PUNCT: delete a space adjacent to a range/citation
    # separator (typographic dash / "/" enum-comma clitic-colon list-paren). Symmetric, pure-
    # space; the separator glyph and every digit/letter survive, so the skeleton is preserved.
    sep_normed = _WS_SEP_RE.sub("", punct_normed)
    if sep_normed != punct_normed:
        fired.add(EncodingFold.WHITESPACE_SEP)

    return sep_normed, frozenset(fired)


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
    fired = lf | rf

    # Output-sparse WHITESPACE_MIDWORD fallback: only when the two texts still DIFFER after
    # every other inert fold do we try deleting letter-letter spaces (the pdfium mid-word-space
    # artifact), and we adopt it ONLY if it makes them equal. A pair already decided (equal, or
    # differing by something the fold cannot touch) is untouched, so the fold is never recorded
    # spuriously and the residual handed to adjudication keeps its word boundaries.
    if left_canon != right_canon:
        left_ms = _MIDWORD_SPACE_RE.sub("", left_canon)
        right_ms = _MIDWORD_SPACE_RE.sub("", right_canon)
        if left_ms == right_ms and (left_ms != left_canon or right_ms != right_canon):
            fired = fired | {EncodingFold.WHITESPACE_MIDWORD}
            left_canon, right_canon = left_ms, right_ms

    return TextEquivalence(
        equal=left_canon == right_canon,
        folds=tuple(sorted(fired)),
        left_canon=left_canon,
        right_canon=right_canon,
    )
