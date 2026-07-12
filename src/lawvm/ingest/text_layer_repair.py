"""Text-layer REPAIR — validated glyph-substitution token repair (§8).

The REPAIR sibling of :mod:`lawvm.ingest.suspect_region`. That module DETECTS a
text-layer-quality defect (``lexical_implausibility`` /
``cross_reader_disagrees`` / ``more_plausible`` / ``_bigram_plausibility``);
this one REPAIRS a *specific, known* glyph confusion — deterministically and only
when an INDEPENDENT constraint confirms the repair is plausible. The pair is the
detect/repair seam for text-layer fidelity; keep them findable together.

The problem it generalizes
==========================
An embedded PDF font can render one glyph AS ANOTHER, so a token arrives in the
text layer with a wrong-but-legible shape: a Finnish statute citation ``/`` that
renders as ``1`` (``(1505/1992)`` → ``(150511992)``), an ``l`` read as ``1``, an
``O`` read as ``0``, an ``rn`` read as ``m``. The mis-read is not garbled sludge
(``suspect_region``'s lexical detector will not fire — the token still looks like
a plausible number/word); it silently defeats a downstream recognizer whose
anchor expects the *intended* shape.

Blindly substituting the intended glyph everywhere would corrupt genuine tokens
(a real ``1`` is far more common than a mis-read ``/``). The discipline that
makes a substitution SAFE is the same one ``suspect_region`` uses for a re-read:
**adopt the repair only when an independent validator confirms it**. Here the
validator is a *constraint on the restored token* — a year sitting in a plausible
statute-year band, a checksum, a known enumerated shape, or (the phase-5
direction) agreement with a second, independently-produced reader. The mechanism
is jurisdiction- and language-agnostic; only the corrupt SHAPE, the intended
glyph, and the plausibility CONSTRAINT are caller-specific surface.

The general contract
====================
:func:`repair_glyph_substitution` is the thin, well-named home for this. A caller
supplies:

* ``corrupt_re`` — a compiled pattern matching the *corrupted* token shape, with
  capture groups for the parts to carry into the restored token. The pattern
  encodes the caller's confusion (which glyph, in which surrounding shape).
* ``restore`` — a :meth:`re.Match.expand` template that rebuilds the *intended*
  token from those groups (e.g. ``r"(\1/\2)"`` re-inserts the ``/``).
* ``is_plausible`` — the INDEPENDENT validator, ``Match -> bool``. The repair is
  adopted for a match ONLY when this returns ``True``; otherwise the original
  substring is left byte-identical. Defaults to *always plausible* for a
  confusion whose shape is already unambiguous, but the value of the seam is that
  a caller can gate on a constraint the corrupted shape alone cannot guarantee.

This is deliberately NOT a framework: it is one ``re.sub`` with a validated
replacer. Its worth is the DISCOVERABLE SEAM plus a single place to accumulate
known glyph confusions as registered callers, rather than a scatter of one-off
``_repair_*`` helpers each re-deriving the "restore-then-validate" discipline.

Known / anticipated glyph confusions (grow this catalog as callers appear):

* ``/`` ↔ ``1`` — a parenthesised statute citation slash mis-read as a digit;
  validated by a plausible year band. First caller:
  ``lawvm.tools.fi_he_ir_compare._repair_slash_as_one_cites`` (FI/EU cite shape +
  1600–2099 band — the only FI-specific surface; the mechanic here is general).
* ``l`` ↔ ``1`` / ``O`` ↔ ``0`` / ``rn`` ↔ ``m`` — classic OCR/font confusions,
  each validated by a shape or checksum constraint on the restored token.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from lawvm.ingest.suspect_region import more_plausible

__all__ = [
    "repair_glyph_substitution",
    "TokenSubstitution",
    "ReconcileResult",
    "reconcile_vision_tokens",
]


def repair_glyph_substitution(
    text: str,
    *,
    corrupt_re: "re.Pattern[str]",
    restore: str,
    is_plausible: Callable[["re.Match[str]"], bool] = lambda _m: True,
) -> str:
    """Restore a known glyph substitution in ``text``, gated by an independent validator.

    For every non-overlapping match of ``corrupt_re`` (the corrupted token shape),
    rebuild the intended token via ``restore`` (a :meth:`re.Match.expand` template
    over the match's groups) — but ADOPT the rebuilt token ONLY when
    ``is_plausible(match)`` is ``True``. A match the validator rejects is left
    BYTE-IDENTICAL (its original substring is returned), so a genuine token that
    merely resembles the corrupted shape is never mangled into a phantom.

    Pure and deterministic: no model, no I/O. The independence that makes a
    substitution safe lives entirely in ``is_plausible`` — a constraint on the
    restored token (a value band, a checksum, a known shape) or, in the phase-5
    direction, agreement with a second independently-produced reader. See the
    module docstring for the general contract and the known-confusion catalog, and
    :mod:`lawvm.ingest.suspect_region` for the DETECTION sibling.
    """

    def _replace(match: "re.Match[str]") -> str:
        if is_plausible(match):
            return match.expand(restore)
        return match.group(0)

    return corrupt_re.sub(_replace, text)


# --------------------------------------------------------------------------- #
# Vision-witness token reconciliation — the phase-5 second-reader validator.    #
# --------------------------------------------------------------------------- #
#
# The general :func:`repair_glyph_substitution` above validates a repair against a
# CONSTRAINT on the restored token (a value band). The module docstring names the
# phase-5 direction as validating instead against *"agreement with a second,
# independently-produced reader"*. This is that validator, for the case a fixed
# ``corrupt_re`` cannot express: a CORRUPT-FONT text layer whose broken CMap maps
# glyphs to wrong-but-legible shapes — MANY distinct, per-document confusions
# (``Karvausoikeuden`` for ``Korvausoikeuden`` [o→a], ``tietoJen`` for ``tietojen``
# [J→j], ``erotlelu`` for ``erottelu`` [l→t]). No single substitution rule fixes
# these, and they are lexically plausible, so ``suspect_region``'s garble detector
# never fires. A SECOND reader that reads the rendered PAGE PIXELS (a vision witness),
# produced INDEPENDENTLY of the broken CMap, recovers the intended text.
#
# This function is the PURE, deterministic reconciliation core: given the geom read
# of a span and one (or two) independent vision read(s) that COVER it, it token-aligns
# the reads and substitutes a geom token with the vision token ONLY where the
# substitution is provably-safe. It performs NO I/O and calls NO model — the caller
# supplies the vision text (from a content-addressed store; see the FI wiring in
# ``fi_he_ir_compare`` and the store in ``recovered_text_store``).
#
# PRECISION-FIRST / NON-MASKING is the binding constraint. A false substitution that
# could hide a genuine PDF-vs-XML difference is FORBIDDEN. There are TWO gates, each
# non-masking by its own structural argument; a caller enables the second by passing a
# second independent read.
#
# GATE A — SINGLE-LETTER glyph confusion (one vision read; the shipped behaviour, kept
# byte-identical). A geom↔vision disagreement is repaired only when:
#   (A1) AGREEMENT PRESERVES. Substitution happens ONLY where the reads DISAGREE at a
#        token (an isolated 1:1 REPLACE opcode). Where they agree, the geom token is
#        byte-identical — a genuine difference the pixels corroborate is never touched.
#   (A2) SINGLE-LETTER GLYPH SHAPE. The two tokens are the SAME length and differ in
#        EXACTLY ONE position, a LETTER in both (a font mapping one glyph to another).
#        A different word, a multi-character corruption, or a digit is left untouched.
#   (A3) NON-WORSENING. The vision token is not strictly LESS plausible than the geom
#        token (``not more_plausible(geom, vision)``) — a degenerate MISREAD is rejected.
#
# GATE B — MULTI-CHARACTER corrupt-font confusion (requires TWO independent reads). A
# broken CMap can map a whole cluster of glyphs (``periruisestä`` for ``perimisestä``,
# ``työttömyyskassaha`` for ``työttömyyskassalta``) — legible, so ``suspect_region`` never
# fires, and NOT a single-letter shape, so Gate A cannot touch it. We correct the geom
# token to WHAT THE PIXELS SHOW (the vision reads), NEVER to a lexicon "valid word" and
# NEVER to anything derived from the XML answer key. It is non-masking BY CONSTRUCTION:
# if the pixels genuinely show word P (≠ the XML's X), the vision reads P, we substitute
# P, and the op body still holds P ≠ X — the genuine difference SURVIVES (we corrected
# toward the pixels, not the answer key). The ONE residual hole is a vision MISREAD of P
# landing exactly on X; it is closed by requiring TWO INDEPENDENT reads to AGREE on the
# replacement (two independent misreads coinciding on X is vanishingly unlikely — the
# goal's "≥2 independent readers agree" corroboration standard). A geom token is replaced
# only when ALL hold:
#   (B1) an isolated 1:1 REPLACE opcode vs BOTH reads (agreement preserves, as A1);
#   (B2) the two independent reads AGREE on the replacement token (consensus), the reads
#        being independent via a DIFFERENT render scale (and/or a second blind prompt);
#   (B3) HIGH char-similarity between the geom token and the consensus token
#        (:func:`_high_char_similarity` — a bounded difflib ratio), so the replacement is
#        a corrupt READ of the SAME underlying word, not a wholesale different token.
#   The replacement is the CONSENSUS vision token — never anything read from the XML.
# This is language-, script- and jurisdiction-agnostic: it uses only pixel consensus and
# a generic character-similarity bound, no dictionary / morphology / lexicon oracle.
#
# For BOTH gates the CALLER additionally never folds a reconciled body into the
# deterministic ``exact`` headline — a vision recovery is a SEPARATELY-bucketed, receipted
# ``corroborated`` candidate, not a certification.


@dataclass(frozen=True, slots=True)
class TokenSubstitution:
    """One adopted glyph-confusion substitution (geom token → vision token)."""

    geom_token: str
    vision_token: str


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """The outcome of reconciling a geom span against an independent vision read."""

    repaired_text: str
    substitutions: Tuple[TokenSubstitution, ...]

    @property
    def changed(self) -> bool:
        return bool(self.substitutions)


_TOKEN_RE = re.compile(r"\S+")

#: The MULTI-CHARACTER consensus gate's (Gate B) character-similarity floor: the geom
#: token and the two-read CONSENSUS token must share at least this difflib ratio (2·M/T
#: over matched characters M and total length T) to be adopted. A corrupt-font READ of a
#: word preserves nearly all of it (``periruisestä``↔``perimisestä`` ≈ 0.87,
#: ``työttömyyskassaha``↔``työttömyyskassalta`` ≈ 0.91), while a wholesale DIFFERENT word of
#: similar length scores below (``Potilasvakuutuskeskus``↔``potilasvakuutusyhdistys`` ≈ 0.73).
#: 0.75 sits just above that different-word band and below every genuine multi-char garble
#: observed, so a wholesale different token cannot pass this conservative guard.
#: (Masking is already precluded structurally — we substitute toward the pixel consensus,
#: not the XML — so this bound is a churn/precision guard, not the non-masking mechanism.)
_MULTICHAR_SIMILARITY_MIN = 0.75


def _high_char_similarity(geom_tok: str, vision_tok: str) -> bool:
    """Is the two-read consensus token a corrupt READ of the geom token (Gate B3)?

    A bounded character-level :class:`difflib.SequenceMatcher` ratio ≥
    :data:`_MULTICHAR_SIMILARITY_MIN` — high enough that only a corrupt reading of the SAME
    underlying word passes, so a genuinely different token (which the pixel-consensus logic
    would substitute non-maskingly anyway) is conservatively left byte-identical. Language-
    and script-agnostic (pure character overlap; no dictionary).
    """
    return (
        difflib.SequenceMatcher(None, geom_tok, vision_tok, autojunk=False).ratio()
        >= _MULTICHAR_SIMILARITY_MIN
    )


def _candidate_replacements(
    geom_tokens: list[str], vision_text: str
) -> "dict[int, str]":
    """Map each geom-token index to its aligned vision token at an isolated 1:1 REPLACE.

    Token-aligns ``geom_tokens`` to the anchored window of ``vision_text`` and returns
    ``{geom_index: vision_token}`` for every position where the two reads DISAGREE as an
    ISOLATED 1:1 REPLACE opcode (guard 1 / B1) — the raw candidate disagreements, with NO
    plausibility / shape / similarity gate applied (the caller gates). An empty/whitespace
    read (the witness could not read the region) yields ``{}`` — absence is never a
    candidate. Pure and deterministic; reuses :func:`_anchored_vision_window`.
    """
    if not vision_text.strip():
        return {}
    vision_window = _anchored_vision_window(geom_tokens, _TOKEN_RE.findall(vision_text))
    out: dict[int, str] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        None, geom_tokens, vision_window, autojunk=False
    ).get_opcodes():
        if tag == "replace" and (i2 - i1) == 1 and (j2 - j1) == 1:
            out[i1] = vision_window[j1]
    return out


def _is_single_letter_glyph_confusion(geom_tok: str, vision_tok: str) -> bool:
    """Are the two tokens the SAME length, differing in EXACTLY ONE LETTER position?

    The provably-safe glyph-confusion shape (guard 2): a broken font mapped one letter
    glyph to another (``o``→``a``, ``J``→``j``, ``l``→``t``), so the tokens are the same
    length and differ in one position, and that position is a letter in BOTH reads. A
    different word (multi-char / length-different) or a digit/punct difference (a cite
    year, an enumerator) is NOT this shape and is rejected.
    """
    if geom_tok == vision_tok or len(geom_tok) != len(vision_tok):
        return False
    diff_idx = -1
    for i, (g, v) in enumerate(zip(geom_tok, vision_tok, strict=True)):
        if g != v:
            if diff_idx != -1:  # a second differing position → not single-char
                return False
            diff_idx = i
    if diff_idx == -1:
        return False
    return geom_tok[diff_idx].isalpha() and vision_tok[diff_idx].isalpha()


def _anchored_vision_window(
    geom_tokens: list[str], vision_tokens: list[str]
) -> list[str]:
    """The slice of ``vision_tokens`` positionally aligned to ``geom_tokens``.

    A vision read of a rendered PAGE covers MORE than one op body (page furniture,
    headings, neighbouring sections). Aligning the whole page directly leaves a corrupt
    token at a body BOUNDARY absorbed into a giant page-preamble replace block (so its
    single vision counterpart is never a 1:1 opcode). We first pin the geom span onto the
    vision token stream via the longest matching blocks, then EXTEND the window by the
    number of unmatched geom tokens on each side, so the body's boundary tokens align 1:1
    inside the window. Returns the whole vision list unchanged when no anchor is found.
    """
    blocks = [
        b
        for b in difflib.SequenceMatcher(
            None, geom_tokens, vision_tokens, autojunk=False
        ).get_matching_blocks()
        if b.size > 0
    ]
    if not blocks:
        return vision_tokens
    first, last = blocks[0], blocks[-1]
    start = max(0, first.b - first.a)
    end = min(
        len(vision_tokens),
        last.b + last.size + (len(geom_tokens) - last.a - last.size),
    )
    return vision_tokens[start:end]


def reconcile_vision_tokens(
    geom_text: str,
    vision_text: str,
    *,
    vision_text_2: Optional[str] = None,
    is_glyph_confusion: Optional[Callable[[str, str], bool]] = None,
) -> ReconcileResult:
    """Reconcile a geom span against one (or two) independent vision read(s).

    Token-aligns ``geom_text`` (the deterministic geom read, produced through a possibly
    CORRUPT font CMap) to ``vision_text`` (an INDEPENDENT read of the same rendered region,
    produced from PIXELS) and returns ``geom_text`` with each safely-identified glyph
    confusion substituted by the vision token — every other token BYTE-IDENTICAL.

    Pure and deterministic: no I/O, no model. An empty/whitespace ``vision_text`` (the
    witness could not read the region) yields the geom text UNCHANGED — absence of a
    second read is never treated as a correction.

    Two gates run per disagreeing token (module section header proves each non-masking):

    * **Gate A — single-letter** (``vision_text`` only; the shipped behaviour, byte-identical
      to before when ``vision_text_2`` is ``None``): an isolated 1:1 REPLACE where
      ``is_glyph_confusion(geom, vision)`` (default :func:`_is_single_letter_glyph_confusion`,
      a same-length single-LETTER difference) AND ``not more_plausible(geom, vision)``.
    * **Gate B — multi-character consensus** (needs ``vision_text_2``): an isolated 1:1
      REPLACE vs BOTH reads where the two INDEPENDENT reads AGREE on the replacement token
      and it is a HIGH-char-similarity (:func:`_high_char_similarity`) corrupt read of the
      geom token. The replacement is the CONSENSUS vision token (what the pixels show), never
      anything derived from the XML — so a genuine PDF≠XML difference survives (we correct
      toward the pixels, not the answer key). ``vision_text_2`` blank / ``None`` disables
      Gate B, so a single witness never triggers a multi-char substitution.

    Gate A is tried first; a token it adopts is not re-considered by Gate B (single-letter
    confusions keep their exact shipped behaviour). Language-/script-agnostic throughout: no
    dictionary, morphology, or lexicon — only pixel consensus and character similarity.
    """
    gate = is_glyph_confusion or _is_single_letter_glyph_confusion
    if not vision_text.strip():
        return ReconcileResult(geom_text, ())
    geom_tokens = _TOKEN_RE.findall(geom_text)
    if not geom_tokens:
        return ReconcileResult(geom_text, ())
    cand1 = _candidate_replacements(geom_tokens, vision_text)
    cand2 = (
        _candidate_replacements(geom_tokens, vision_text_2)
        if vision_text_2 is not None
        else {}
    )
    out = list(geom_tokens)
    subs: list[TokenSubstitution] = []
    for i, geom_tok in enumerate(geom_tokens):
        vision_tok = cand1.get(i)
        if vision_tok is None:
            continue  # reads AGREE here (or no isolated 1:1 opcode) → untouched (A1/B1)
        # Gate A — single-letter glyph confusion, non-worsening (one read).
        if gate(geom_tok, vision_tok) and not more_plausible(geom_tok, vision_tok):
            out[i] = vision_tok
            subs.append(TokenSubstitution(geom_tok, vision_tok))
            continue
        # Gate B — multi-character pixel consensus: the SECOND independent read must AGREE
        # on the SAME replacement token, which must be a high-similarity corrupt read of the
        # geom token. Substitute toward that consensus (the pixels), never toward the XML.
        if (
            cand2.get(i) == vision_tok
            and _high_char_similarity(geom_tok, vision_tok)
        ):
            out[i] = vision_tok
            subs.append(TokenSubstitution(geom_tok, vision_tok))
    if not subs:
        return ReconcileResult(geom_text, ())
    return ReconcileResult(" ".join(out), tuple(subs))
