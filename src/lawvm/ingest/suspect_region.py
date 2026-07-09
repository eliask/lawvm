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

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

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
# Lexical implausibility — cheap, pure string scoring (no model, no lib).      #
# --------------------------------------------------------------------------- #

# Long space-less alphabetic runs are the hallmark of a merged / garbled OCR
# blob: ordinary Finnish + English words (even long compounds) break with spaces,
# hyphens, or punctuation well before this. Chosen conservatively so genuine long
# compounds (``sopimusvelvoitteista``, 20) do not fire; a garble like
# ``sopimusekertaluonteestisaatavien`` (30+) does.
_LONGEST_ALPHA_RUN_FLOOR = 26

# Finnish + English are both vowel-rich; a run with almost no vowels (or almost
# all vowels) is implausible text. Bounds chosen to admit ordinary text and flag
# consonant/vowel-degenerate garbles.
_VOWEL_RATIO_LOW = 0.12
_VOWEL_RATIO_HIGH = 0.80
_VOWELS = frozenset("aeiouyäöåAEIOUYÄÖÅ")

# A light plausibility profile: character bigrams that occur commonly in Finnish
# and English body text. A long alpha run whose bigrams are mostly OUTSIDE this
# set is likely garbled. This is a coarse affordance, not a language model — it
# only needs to separate "looks like words" from "looks like OCR sludge".
_COMMON_BIGRAMS = frozenset(
    # Finnish-frequent
    [
        "en", "in", "an", "on", "ta", "st", "ss", "ll", "is", "se", "te", "el",
        "at", "aa", "ii", "ee", "ki", "ne", "ka", "la", "na", "ni", "si", "ti",
        "va", "ja", "va", "es", "et", "as", "us", "ää", "yy", "tä", "än", "sä",
        # English-frequent
        "th", "he", "er", "re", "nd", "ou", "ea", "ng", "al", "it", "ar", "or",
        "to", "nt", "ed", "ha", "of", "ver", "co", "de", "ro", "le", "me", "ent",
    ]
)


def _vowel_ratio(text: str) -> Optional[float]:
    """Vowel fraction over the alphabetic characters (``None`` if too few letters)."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return None
    vowels = sum(1 for c in letters if c in _VOWELS)
    return vowels / len(letters)


def _bigram_plausibility(run: str) -> float:
    """Fraction of a run's character bigrams that are in the common-bigram profile.

    A low fraction over a LONG run marks OCR sludge (bigrams no natural word
    forms). Short runs are excluded by the caller (too few bigrams to judge).
    """
    r = run.lower()
    bigrams = [r[i : i + 2] for i in range(len(r) - 1)]
    if not bigrams:
        return 1.0
    hits = sum(1 for b in bigrams if b in _COMMON_BIGRAMS)
    return hits / len(bigrams)


# A long run this implausible by bigrams is garbled. Tuned so ordinary Finnish
# compounds (rich in common bigrams) stay above it and OCR sludge falls below.
_BIGRAM_PLAUSIBILITY_FLOOR = 0.18


def lexical_implausibility(text: str) -> Tuple[str, ...]:
    """Deterministic implausibility signals for a text leaf (closed vocab, may be empty).

    Returns the set of fired sub-signals (``vowel_degenerate`` /
    ``low_bigram_plausibility``); EMPTY for clean text (so a clean page surfaces
    zero suspects → zero re-reads). Pure string function — no model, no lib.

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
    ratio = _vowel_ratio(stripped)
    if ratio is not None and (ratio < _VOWEL_RATIO_LOW or ratio > _VOWEL_RATIO_HIGH):
        signals.append("vowel_degenerate")
    # Bigram plausibility judges only a materially-long run (short strings have too
    # few bigrams to separate a garble from a rare-but-real short token). Length is
    # the GATE for judging the run, never a suspect signal in itself.
    longest_run = _longest_run_text(stripped)
    if len(longest_run) >= _LONGEST_ALPHA_RUN_FLOOR and (
        _bigram_plausibility(longest_run) < _BIGRAM_PLAUSIBILITY_FLOOR
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
