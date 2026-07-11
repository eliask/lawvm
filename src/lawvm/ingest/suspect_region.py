"""Suspect-region surfacing — deterministic re-read candidate detection (§8).

The Level-1 vision model can emit *confidently garbled* OCR (a real example:
``sopimusekertaluont-eestisaat…``) as ordinary text: it is NOT flagged
``freeform.garbled_source``, so the read looks clean and Level 2 carries the
garbage through. Level-2 conservatism structurally cannot repair a Level-1 read
defect — the fix must happen where the mis-read is produced.

This module SURFACES candidates only; it NEVER edits. Per the guiding principle
(``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md`` §intro: "mechanics only ever
surface candidates and metadata; the model decides"), a detector returns typed
``SuspectRegion`` marks — the converge loop then asks the model to re-read each
and applies the result only through the existing, already-gated patch mechanism.

Two independent signals, deliberately cheap and deterministic:

* **cross-reader disagreement** (PRIMARY) — an INDEPENDENT reader over the same
  bbox (the pdfium text layer via ``PageLine``, or a docling / nemotron read)
  produces materially different text than the vision read. Disagreement between
  two independently-produced reads is the strongest garble signal.
* **lexical implausibility** (SECONDARY, cheap) — a pure string score of the
  vision text: a degenerate vowel ratio, or a low character-bigram plausibility
  (over a materially-long run) against a light Finnish+English profile. LENGTH
  ALONE never fires — Finnish has genuine 40+-char compounds — so this catches
  degenerate OCR sludge, not a syllable-plausible run-together garble (that is the
  cross-reader signal's job). Catches a garble even when no second reader covers
  the region.

Both are AFFORDANCES, not authority: a suspect mark is a proposal to re-read, and
the re-read still rides the convergence fixpoint + assurance gates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from lawvm.core.source_document.anchors import BBox

# --------------------------------------------------------------------------- #
# Typed carrier — a re-read candidate. NEVER an edit, only a proposal.         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SuspectRegion:
    """One deterministic re-read candidate: a text-leaf the model should re-read.

    ``node_path`` addresses the suspect leaf in the resolved forest (same path
    space as ``page_level._text_leaf_paths``); ``bbox`` is the page region to
    render+re-read (from the matched ``PageLine`` geometry, ``None`` when the leaf
    has no geometry — then no crop can be rendered and the region is un-actionable
    but still recorded). ``signals`` names WHY it fired (a closed vocab:
    ``cross_reader_disagreement`` / ``lexical_implausible``), and ``cross_reader``
    carries the disagreeing independent read (when present) so the re-read gate can
    prefer a re-read that AGREES with it.
    """

    node_path: Tuple[int, ...]
    bbox: Optional[BBox]
    vision_text: str
    signals: Tuple[str, ...]
    cross_reader: Optional[str] = None


# --------------------------------------------------------------------------- #
# Char-class corruption signature — the FREE, jurisdiction-AGNOSTIC scan.       #
# --------------------------------------------------------------------------- #
#
# This is the UNIFICATION home for the deterministic corruption-glyph scan that
# also backs ``fi_appendix_vision_screen.scan_garble`` (which now DELEGATES here —
# one signature, not two copies). It flags, per character, the classic broken-
# ToUnicode-CMap / decode-failure artifacts:
#
#   * a Private-Use-Area codepoint (a font glyph with no Unicode meaning),
#   * a non-whitespace C0/C1 control character (a control byte leaked into text),
#   * the U+FFFD replacement character (a surfaced decode failure), and
#   * a per-mille / dagger mojibake glyph standing where a LETTER belongs.
#
# These signals are LANGUAGE-INDEPENDENT (a control byte or PUA glyph is corruption
# in any jurisdiction — EE/EU/US reuse this unchanged), so no language profile is
# injected here; the language-specific judgement is the SEPARATE lexical layer below.
#
# NOTE — Unicode NONCHARACTERS (U+FFFE/U+FFFF and the U+FDD0–U+FDEF block) are
# DELIBERATELY NOT flagged: in the FI born-digital corpus pdfium routinely emits
# U+FFFE at soft-hyphen / ligature break points inside otherwise-clean text (present
# in 138/140 exact-comparable HEs), so treating it as corruption would false-flood
# the clean set. Only the four signatures above discriminate a garbled layer.

#: The char-class signature kinds (a closed string vocab; mirrored by
#: ``fi_appendix_vision_screen.GarbleKind`` for the appendix-screen JSON surface).
GARBLE_PRIVATE_USE_AREA = "private_use_area"
GARBLE_CONTROL_CHAR = "control_char"
GARBLE_REPLACEMENT_CHAR = "replacement_char"
GARBLE_MOJIBAKE = "mojibake_signature"

# BMP + supplementary Private-Use-Area ranges (inclusive). Plane-15/16 PUA are the
# supplementary blocks fonts stash custom glyphs in when the CMap is broken.
_PUA_RANGES: Tuple[Tuple[int, int], ...] = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)

# Substitution glyphs a broken CMap emits where a LETTER should be. ``‰`` (per-mille)
# is LEGITIMATE next to a number ("5 ‰" promille), so the mojibake rule fires only
# when such a glyph sits AGAINST a letter or in a run — never on a lone figure-‰.
_MOJIBAKE_GLYPHS = frozenset("‰†‡")  # ‰ † ‡


@dataclass(frozen=True, slots=True)
class CharClassHit:
    """One deterministic corruption signature located in a text run (char-class layer)."""

    kind: str
    index: int
    char: str
    codepoint: int


def _in_pua(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _PUA_RANGES)


def _is_bad_control(ch: str) -> bool:
    r"""A C0/C1 control char that is NOT whitespace (``\t``/``\n``/``\r``/… are fine)."""
    cp = ord(ch)
    if not (0x00 <= cp <= 0x1F or 0x80 <= cp <= 0x9F):
        return False
    return not ch.isspace()  # str.isspace() covers \t \n \v \f \r and NEL (U+0085)


def scan_char_class_garble(text: str) -> Tuple[CharClassHit, ...]:
    """Scan a text run for deterministic corruption signatures (free, no vision, no lib).

    The SINGLE authority for the char-class garble signature: a PUA codepoint, a
    non-whitespace C0/C1 control char, the U+FFFD replacement char, or a mojibake glyph
    (per-mille / dagger against a letter or in a run). Clean prose returns ``()``. The
    appendix-screen ``scan_garble`` wraps this (adds context + its ``GarbleKind`` enum)
    rather than re-implementing it.
    """
    src = text or ""
    hits: List[CharClassHit] = []
    for i, ch in enumerate(src):
        cp = ord(ch)
        if ch == "�":
            hits.append(CharClassHit(GARBLE_REPLACEMENT_CHAR, i, ch, cp))
        elif _in_pua(cp):
            hits.append(CharClassHit(GARBLE_PRIVATE_USE_AREA, i, ch, cp))
        elif _is_bad_control(ch):
            hits.append(CharClassHit(GARBLE_CONTROL_CHAR, i, ch, cp))
        elif ch in _MOJIBAKE_GLYPHS:
            prev_ch = src[i - 1] if i > 0 else ""
            next_ch = src[i + 1] if i + 1 < len(src) else ""
            against_letter = prev_ch.isalpha() or next_ch.isalpha()
            in_run = prev_ch in _MOJIBAKE_GLYPHS or next_ch in _MOJIBAKE_GLYPHS
            if against_letter or in_run:
                hits.append(CharClassHit(GARBLE_MOJIBAKE, i, ch, cp))
    return tuple(hits)


# --------------------------------------------------------------------------- #
# Lexical profile — the INJECTED language surface for the lexical layer.        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LexicalProfile:
    """An injected language surface for :func:`lexical_implausibility`.

    The lexical layer is the ONLY language-dependent part of the garble primitive, so
    the language profile is a PARAMETER (default = the current Finnish+English surface)
    rather than baked in — EE/EU/US reuse the same primitive with their own profile.
    ``vowels`` / ``common_bigrams`` define what "reads like words" looks like; the
    floors bound the vowel balance, the run length below which a run is not judged, and
    the bigram-plausibility floor a long run must clear.
    """

    name: str
    vowels: frozenset
    common_bigrams: frozenset
    vowel_ratio_low: float
    vowel_ratio_high: float
    longest_run_floor: int
    bigram_plausibility_floor: float


#: The default Finnish+English lexical surface (behaviour-preserving for existing
#: callers who pass no profile). Finnish + English are both vowel-rich, so a run with
#: almost no vowels (or almost all) is implausible; the bigram set is coarse "looks like
#: body text" evidence, not a language model.
FI_EN_LEXICAL_PROFILE = LexicalProfile(
    name="fi_en",
    vowels=frozenset("aeiouyäöåAEIOUYÄÖÅ"),
    common_bigrams=frozenset(
        [
            # Finnish-frequent
            "en", "in", "an", "on", "ta", "st", "ss", "ll", "is", "se", "te", "el",
            "at", "aa", "ii", "ee", "ki", "ne", "ka", "la", "na", "ni", "si", "ti",
            "va", "ja", "va", "es", "et", "as", "us", "ää", "yy", "tä", "än", "sä",
            # English-frequent
            "th", "he", "er", "re", "nd", "ou", "ea", "ng", "al", "it", "ar", "or",
            "to", "nt", "ed", "ha", "of", "ver", "co", "de", "ro", "le", "me", "ent",
        ]
    ),
    # Long space-less alpha runs are the hallmark of merged/garbled OCR, but Finnish
    # has genuine 40+-char compounds — LENGTH ALONE never fires; the run must ALSO read
    # implausibly. Chosen so ``sopimusvelvoitteista`` (20) is fine, sludge (30+) fires.
    vowel_ratio_low=0.12,
    vowel_ratio_high=0.80,
    longest_run_floor=26,
    bigram_plausibility_floor=0.18,
)

#: Back-compat default used when no profile is injected.
DEFAULT_LEXICAL_PROFILE = FI_EN_LEXICAL_PROFILE


# --------------------------------------------------------------------------- #
# Lexical implausibility — cheap, pure string scoring (no model, no lib).      #
# --------------------------------------------------------------------------- #


def _vowel_ratio(text: str, profile: LexicalProfile) -> Optional[float]:
    """Vowel fraction over the alphabetic characters (``None`` if too few letters)."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return None
    vowels = sum(1 for c in letters if c in profile.vowels)
    return vowels / len(letters)


def _bigram_plausibility(run: str, profile: LexicalProfile) -> float:
    """Fraction of a run's character bigrams that are in the profile's common-bigram set.

    A low fraction over a LONG run marks OCR sludge (bigrams no natural word
    forms). Short runs are excluded by the caller (too few bigrams to judge).
    """
    r = run.lower()
    bigrams = [r[i : i + 2] for i in range(len(r) - 1)]
    if not bigrams:
        return 1.0
    hits = sum(1 for b in bigrams if b in profile.common_bigrams)
    return hits / len(bigrams)


def lexical_implausibility(
    text: str, *, profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE
) -> Tuple[str, ...]:
    """Deterministic implausibility signals for a text leaf (closed vocab, may be empty).

    Returns the set of fired sub-signals (``vowel_degenerate`` /
    ``low_bigram_plausibility``); EMPTY for clean text (so a clean page surfaces
    zero suspects → zero re-reads). Pure string function — no model, no lib.

    ``profile`` is the INJECTED language surface (default = Finnish+English) so the
    SAME primitive serves EE/EU/US by swapping the profile — the only language-
    dependent part of the garble detector.

    LENGTH ALONE NEVER FIRES: Finnish has genuinely long single-word compounds
    (``epäjärjestelmällistyttämättömyydellänsäkäänköhän``, 48 chars) that are NOT
    garbles. A long space-less run is only a suspect when it ALSO reads
    implausibly — its character bigrams are unlike natural word forms
    (``low_bigram_plausibility``) or its vowel balance is degenerate
    (``vowel_degenerate``). A real compound (rich in common bigrams, normal vowel
    ratio) stays clean; OCR sludge (``sopimusekertaluont-eestisaat…``) fires.
    """
    signals: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ()
    ratio = _vowel_ratio(stripped, profile)
    if ratio is not None and (
        ratio < profile.vowel_ratio_low or ratio > profile.vowel_ratio_high
    ):
        signals.append("vowel_degenerate")
    # Bigram plausibility judges only a materially-long run (short strings have too
    # few bigrams to separate a garble from a rare-but-real short token). Length is
    # the GATE for judging the run, never a suspect signal in itself.
    longest_run = _longest_run_text(stripped)
    if len(longest_run) >= profile.longest_run_floor and (
        _bigram_plausibility(longest_run, profile) < profile.bigram_plausibility_floor
    ):
        signals.append("low_bigram_plausibility")
    return tuple(signals)


def _longest_run_text(text: str) -> str:
    """The literal longest maximal alphabetic run (for bigram scoring)."""
    best = ""
    cur_start = None
    for i, ch in enumerate(text):
        if ch.isalpha():
            if cur_start is None:
                cur_start = i
            if i - cur_start + 1 > len(best):
                best = text[cur_start : i + 1]
        else:
            cur_start = None
    return best


# --------------------------------------------------------------------------- #
# Unified garble signature + whole-READ decision (char-class ∪ lexical).        #
# --------------------------------------------------------------------------- #
#
# The two independent detectors above — the char-class scan (:func:`scan_char_class_
# garble`) and the lexical layer (:func:`lexical_implausibility`) — are UNIFIED here
# into one signature and one read-level decision, so a read-boundary caller gets a
# single first-class garble verdict instead of re-inventing a third detector. The
# whole-READ decision is char-class DENSITY (a corrupt-font text layer is dominated by
# control / PUA / replacement glyphs), NOT a raw hit count: a clean born-digital read
# carries a handful of stray PUA glyphs at ~0 density (measured: max ~1e-5 over the FI
# HE exact set), while a corrupt-font layer runs 0.07–0.94 — a wide, safe gap. The
# lexical layer stays the per-REGION signal (a single garbled leaf, where a long sludge
# run is the tell); at document scale one odd token cannot flip a whole clean read.


@dataclass(frozen=True, slots=True)
class GarbleSignature:
    """The unified deterministic garble signature over one text run.

    Combines the char-class corruption hits (:func:`scan_char_class_garble`) and the
    lexical implausibility signals (:func:`lexical_implausibility`). ``scanned_chars``
    is the non-whitespace char count the fraction is taken over. This is a SIGNATURE
    (evidence), not a verdict — :func:`is_read_garbled` / :func:`is_pervasively_garbled`
    apply the density thresholds a caller needs.
    """

    char_class_hits: Tuple[CharClassHit, ...]
    lexical_signals: Tuple[str, ...]
    scanned_chars: int

    @property
    def clean(self) -> bool:
        return not self.char_class_hits and not self.lexical_signals

    @property
    def char_class_kinds(self) -> Tuple[str, ...]:
        seen = {h.kind for h in self.char_class_hits}
        order = (
            GARBLE_PRIVATE_USE_AREA,
            GARBLE_CONTROL_CHAR,
            GARBLE_REPLACEMENT_CHAR,
            GARBLE_MOJIBAKE,
        )
        return tuple(k for k in order if k in seen)

    @property
    def char_class_fraction(self) -> float:
        """Fraction of non-space chars that are a char-class corruption glyph."""
        if self.scanned_chars <= 0:
            return 0.0
        return len(self.char_class_hits) / self.scanned_chars

    @property
    def signals(self) -> Tuple[str, ...]:
        """All fired signal names (char-class kinds + lexical), stable order."""
        return self.char_class_kinds + self.lexical_signals


#: Whitespace-stripping regex for the density denominator (matches the length gates
#: at the read boundaries so the fraction is over the same char population).
_WS_RE = re.compile(r"\s+")


def garble_signature(
    text: str, *, profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE
) -> GarbleSignature:
    """The unified garble signature for a text run (char-class ∪ lexical, free/no-vision)."""
    src = text or ""
    return GarbleSignature(
        char_class_hits=scan_char_class_garble(src),
        lexical_signals=lexical_implausibility(src, profile=profile),
        scanned_chars=len(_WS_RE.sub("", src)),
    )


#: A whole read this corruption-dense IS garbled (a vision-escalation candidate), NOT a
#: clean text layer. Floor sits far above the clean ceiling (~1e-5) and below the
#: lightest real garble (0.066), with a min absolute hit count so a tiny run with one
#: stray glyph never trips.
_MATERIAL_GARBLE_FRACTION_FLOOR = 0.02
_MATERIAL_GARBLE_MIN_HITS = 8

#: A read this corruption-DOMINATED is garbage end to end (a corrupt-font text layer):
#: divert it at the read boundary rather than return raw garbage. Set high enough that a
#: clean read with a small garbled region (e.g. one scanned appendix) is NOT diverted —
#: it still gets a chance to parse its clean clause; only a pervasively-corrupt layer
#: (0.30+; the corrupt-font case measured 0.94) is refused at the boundary.
_PERVASIVE_GARBLE_FRACTION_FLOOR = 0.30


def is_read_garbled(
    text: str,
    *,
    profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE,
    fraction_floor: float = _MATERIAL_GARBLE_FRACTION_FLOOR,
    min_hits: int = _MATERIAL_GARBLE_MIN_HITS,
) -> bool:
    """Is this whole READ materially garbled (a first-class vision-escalation candidate)?

    True when the char-class corruption DENSITY clears ``fraction_floor`` (with at least
    ``min_hits`` hits, so a short run with a stray glyph never trips). Density, not a raw
    count: a large clean read may carry a few PUA glyphs at ~0 density and stays clean.
    Used to type a no-clause read ``garble_suspect`` instead of silently benign.
    """
    sig = garble_signature(text, profile=profile)
    n = len(sig.char_class_hits)
    return n >= min_hits and sig.char_class_fraction >= fraction_floor


def is_pervasively_garbled(
    text: str, *, profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE
) -> bool:
    """Is this read corruption-DOMINATED end to end (a corrupt-font text layer)?

    The read-boundary gate: True only when char-class corruption dominates the whole
    read (fraction ≥ :data:`_PERVASIVE_GARBLE_FRACTION_FLOOR`), so a partially-garbled
    read (clean clause + one garbled region) is NOT refused — it flows on and, if it
    then yields no clause, is typed ``garble_suspect`` by :func:`is_read_garbled`.
    """
    return is_read_garbled(
        text, profile=profile, fraction_floor=_PERVASIVE_GARBLE_FRACTION_FLOOR
    )


def garble_reason(
    text: str, *, profile: LexicalProfile = DEFAULT_LEXICAL_PROFILE
) -> str:
    """A short, honest reason line for a garbled read (kinds + density) — for typed detail."""
    sig = garble_signature(text, profile=profile)
    kinds = ",".join(sig.signals) or "none"
    pct = sig.char_class_fraction * 100.0
    return (
        f"garbled text layer: {len(sig.char_class_hits)} corruption glyphs "
        f"({pct:.1f}% of {sig.scanned_chars} non-space chars; kinds={kinds}) — "
        "vision re-read escalation candidate"
    )


# --------------------------------------------------------------------------- #
# Cross-reader disagreement — an independent read over the same region.        #
# --------------------------------------------------------------------------- #


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _token_agreement(a: str, b: str) -> float:
    """Jaccard over the two reads' word sets (1.0 = identical words, 0.0 = disjoint)."""
    wa = {w for w in _normalize(a).split() if w}
    wb = {w for w in _normalize(b).split() if w}
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    inter = len(wa & wb)
    union = len(wa | wb)
    return inter / union if union else 1.0


# Below this word-agreement between the vision read and an independent reader over
# the SAME region, the two reads MATERIALLY disagree → a re-read candidate. Set so
# an identical or near-identical read (minor punctuation) does NOT fire.
_CROSS_READER_AGREEMENT_FLOOR = 0.6


def cross_reader_disagrees(vision_text: str, independent_text: str) -> bool:
    """Do the vision read and an INDEPENDENT read of the same region disagree?

    Materially different word sets (Jaccard below the floor) between two
    independently-produced reads is the primary garble signal. Empty independent
    text (the reader could not read the region) does NOT fire — absence of a
    second read is not disagreement (never invent a suspect from silence).
    """
    if not independent_text.strip() or not vision_text.strip():
        return False
    return _token_agreement(vision_text, independent_text) < _CROSS_READER_AGREEMENT_FLOOR


def more_plausible(candidate: str, incumbent: str) -> bool:
    """Is ``candidate`` (a re-read) a strictly less-implausible read than ``incumbent``?

    The re-read GATE: a re-read replaces the suspect leaf only if it is more
    plausible than the current text. "More plausible" = fewer fired lexical
    implausibility signals (a garble → clean read drops signals to zero). Ties do
    NOT replace (conservative — no churn on an equally-plausible re-read).
    """
    if not candidate.strip():
        return False
    cand_bad = len(lexical_implausibility(candidate))
    inc_bad = len(lexical_implausibility(incumbent))
    return cand_bad < inc_bad


# --------------------------------------------------------------------------- #
# Region surfacing — walk the resolved leaves, fire the two signals.           #
# --------------------------------------------------------------------------- #


# A cross-reader lane: given a page region's text, return an independent reader's
# text for the SAME region (or ""). ``page_lines`` (the pdfium text layer) is the
# default, always-available independent reader — its per-line geometry lets a
# region be matched by bbox overlap. docling / nemotron plug in via the same
# ``region_text`` shape when available (they read the same page independently).
def pdfium_region_text(bbox: Optional[BBox], page_lines: Sequence[object]) -> str:
    """Independent read of a region from the pdfium text layer (``PageLine``s).

    Concatenates the text of every ``PageLine`` whose bbox VERTICALLY overlaps the
    suspect region — the deterministic-substrate read of that region, produced
    fully independently of the vision model. Empty when no line covers the region
    (then cross-reader disagreement cannot fire; lexical implausibility still can).
    """
    if bbox is None:
        return ""
    parts: list[str] = []
    for pl in page_lines:
        b = getattr(pl, "bbox", None)
        if b is None:
            continue
        # Vertical overlap in PDF-point space (origin bottom-left).
        if b.y1 >= bbox.y0 and b.y0 <= bbox.y1:
            txt = getattr(pl, "text", "") or ""
            if txt.strip():
                parts.append(txt)
    return " ".join(parts)
