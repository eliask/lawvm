"""``lawvm.ingest.text_layer_repair`` — validated glyph-substitution token repair.

Hermetic, no farchive. Exercises the GENERAL contract of
:func:`repair_glyph_substitution` (restore a known glyph confusion, adopt ONLY
when an independent validator confirms it) and proves the seam is real by
plugging in a SECOND, unrelated confusion (``O``↔``0``) with its own validator —
not a one-off rename of the FI cite helper. The REPAIR sibling of
:mod:`lawvm.ingest.suspect_region` (detection).
"""
from __future__ import annotations

import re

from lawvm.ingest.text_layer_repair import (
    reconcile_vision_tokens,
    repair_glyph_substitution,
)

# --------------------------------------------------------------------------- #
# The general mechanic: restore + independent validator.                       #
# --------------------------------------------------------------------------- #


def test_repair_adopts_only_when_validator_confirms() -> None:
    # A trivial confusion: a "#N" token whose "#" the layer dropped, leaving "xN"; restore the
    # "#" only when N is an even number (a stand-in independent constraint).
    corrupt = re.compile(r"x(\d)")
    def even_only(m):
        return int(m.group(1)) % 2 == 0
    assert repair_glyph_substitution("x2 x3 x4", corrupt_re=corrupt, restore=r"#\1", is_plausible=even_only) == "#2 x3 #4"
    # Default validator (always plausible) restores every match.
    assert repair_glyph_substitution("x2 x3", corrupt_re=corrupt, restore=r"#\1") == "#2 #3"


def test_rejected_match_is_byte_identical() -> None:
    # A match the validator rejects must leave the ORIGINAL substring untouched, not a partial
    # rebuild — so a genuine token that merely resembles the corrupt shape is never mangled.
    corrupt = re.compile(r"x(\d)")
    def never(_m):
        return False
    src = "x1 x9 keep-me"
    assert repair_glyph_substitution(src, corrupt_re=corrupt, restore=r"#\1", is_plausible=never) == src


# --------------------------------------------------------------------------- #
# Generality: a SECOND confusion (O↔0) plugs in trivially with its own          #
# validator — proving the seam is not an FI one-off.                            #
# --------------------------------------------------------------------------- #


def test_second_confusion_O_as_zero_plugs_in() -> None:
    # An ISBN-ish id whose leading "O" the font rendered as "0" ("O123" → "0123"). Restore the
    # "O" only when the whole token is a known FOUR-glyph id shape (validator: length band) — a
    # different confusion, a different validator, ZERO change to the general primitive.
    corrupt = re.compile(r"\b0(\d{3})\b")
    def four_glyph(m):
        return len(m.group(0)) == 4
    assert repair_glyph_substitution("id 0123 here", corrupt_re=corrupt, restore=r"O\1", is_plausible=four_glyph) == "id O123 here"
    # A five-digit number is NOT the id shape → the shape re never matches it, so even the
    # default (always-plausible) validator leaves it byte-identical.
    assert repair_glyph_substitution("qty 01234 units", corrupt_re=corrupt, restore=r"O\1") == "qty 01234 units"


def test_second_confusion_rn_as_m_plugs_in() -> None:
    # The classic "rn"↔"m" OCR confusion: "modem" mis-read as "modern"-ish "moderndemo" — here a
    # toy where "rnodem" should be "modem", gated on a tiny known-word validator.
    corrupt = re.compile(r"\brn(\w+)\b")
    def known(m):
        return m.group(1) in {"odem", "ap"}
    assert repair_glyph_substitution("the rnodem and rnxyz", corrupt_re=corrupt, restore=r"m\1", is_plausible=known) == "the modem and rnxyz"


# --------------------------------------------------------------------------- #
# The FI cite confusion, exercised through the GENERAL primitive directly       #
# (mirrors the registered caller in fi_he_ir_compare).                          #
# --------------------------------------------------------------------------- #


def test_fi_cite_slash_as_one_through_general_primitive() -> None:
    four = re.compile(r"\((\d{1,4})1(\d{4})\)")
    def band(m):
        return 1600 <= int(m.group(2)) <= 2099
    assert repair_glyph_substitution("(150511992)", corrupt_re=four, restore=r"(\1/\2)", is_plausible=band) == "(1505/1992)"
    # A parenthesised number whose trailing 4 digits are not a plausible year is left untouched.
    assert repair_glyph_substitution("(123499999)", corrupt_re=four, restore=r"(\1/\2)", is_plausible=band) == "(123499999)"


# --------------------------------------------------------------------------- #
# The vision-witness token reconciliation (phase-5 second-reader validator).    #
# The independent vision read is a REAL example: a rendered-page transcription   #
# that COVERS the geom span plus surrounding page furniture / headings, so the   #
# window-anchoring (not just a clean equal-length string) is exercised.          #
# --------------------------------------------------------------------------- #

# The corrupt-font geom read of an op body (HE 91/1998: broken CMap maps o→a).
_GEOM_BODY = "Karvausoikeuden edellytykset Korvausta suoritetaan henkilövahingosta jos"
# An INDEPENDENT vision read of the same rendered PAGE — the body PLUS the page's
# running header and section heading before it (what the pixels actually show).
_VISION_PAGE = (
    "HE 91/1998 vp 2 § Korvausoikeuden edellytykset "
    "Korvausta suoritetaan henkilövahingosta jos"
)


def test_reconcile_recovers_single_letter_glyph_confusion() -> None:
    """(a) A corrupt token vision reads correctly (o→a) is substituted; the op body is fixed."""
    res = reconcile_vision_tokens(_GEOM_BODY, _VISION_PAGE)
    assert res.changed
    assert [(s.geom_token, s.vision_token) for s in res.substitutions] == [
        ("Karvausoikeuden", "Korvausoikeuden")
    ]
    # Only the corrupt boundary token changed; every other token byte-identical.
    assert res.repaired_text == _GEOM_BODY.replace("Karvausoikeuden", "Korvausoikeuden")


def test_reconcile_agreement_is_byte_identical() -> None:
    """(b) Where the two independent reads AGREE, the geom text is returned UNCHANGED."""
    geom = "Korvausta suoritetaan henkilövahingosta"
    res = reconcile_vision_tokens(geom, "2 § " + geom + " jos")
    assert not res.changed
    assert res.repaired_text == geom  # byte-identical (no substitution)


def test_reconcile_empty_vision_is_noop() -> None:
    """(d-no-reader analogue) An empty/whitespace vision read never corrects anything."""
    assert reconcile_vision_tokens(_GEOM_BODY, "").repaired_text == _GEOM_BODY
    assert not reconcile_vision_tokens(_GEOM_BODY, "   \n ").changed


def test_reconcile_preserves_a_different_word_non_masking() -> None:
    """(c) A genuine multi-character content difference is NOT a glyph confusion → PRESERVED.

    The PDF proposes a materially different word ("yhdistys") that the vision witness reads
    (agreeing with the geom read that the pixels show it); a naive close-word substitution
    would MASK that real difference, so a >1-character difference is never adopted.
    """
    geom = "kuuluttava potilasvakuutusyhdistykseen Yhdistys hoitaa"
    # Vision reads the SAME real word (the pixels show "yhdistys") — agreement preserves it.
    res = reconcile_vision_tokens(geom, "on kuuluttava potilasvakuutusyhdistykseen Yhdistys hoitaa toiminnan")
    assert not res.changed
    assert res.repaired_text == geom
    # Even if a vision witness read a DIFFERENT real word of a different length, it is not a
    # single-letter glyph confusion, so it is rejected (non-masking).
    res2 = reconcile_vision_tokens("tekee Potilasvakuutuskeskus", "tekee potilasvakuutusyhdistys")
    assert not res2.changed
    assert res2.repaired_text == "tekee Potilasvakuutuskeskus"


def test_reconcile_rejects_less_plausible_vision_token() -> None:
    """(c-plausibility) A single-letter change that makes the vision token LESS plausible is rejected.

    Guard 3 (``not more_plausible``) blocks a vision MISREAD that degrades a token: geom is a
    balanced run, vision differs by ONE letter into a vowel-degenerate run (a lexical
    implausibility signal), so the geom token survives even though the shape is single-letter.
    """
    geom = "aaaacaab tavaraa"  # balanced-ish run (0 implausibility signals)
    vision = "aaaaaaab tavaraa"  # differs by one letter → all-vowel run (vowel_degenerate)
    res = reconcile_vision_tokens(geom, vision)
    assert not res.changed
    assert res.repaired_text == geom


def test_reconcile_does_not_mutate_a_digit_or_cite_difference() -> None:
    """A single-DIGIT difference (a cite year, an enumerator) is NOT a letter glyph confusion.

    The differing position must be a LETTER in both reads, so a year digit is never silently
    rewritten toward a vision read (that repair has its own value-band validator elsewhere).
    """
    geom = "annetun lain (367/1961) nojalla"
    vision = "annetun lain (367/1968) nojalla"  # single-digit difference
    res = reconcile_vision_tokens(geom, vision)
    assert not res.changed
    assert res.repaired_text == geom


# --------------------------------------------------------------------------- #
# Gate B — MULTI-CHARACTER pixel-consensus (TWO independent reads must AGREE).   #
# A broken CMap maps a CLUSTER of glyphs ("periruisestä" for "perimisestä"), not #
# a single letter, so Gate A cannot touch it; the correction is toward the       #
# CONSENSUS of two independent reads (the pixels), never a lexicon / the XML.     #
# --------------------------------------------------------------------------- #

# The corrupt-font geom body: "periruisestä" is a MULTI-character garble of "perimisestä".
_GB_GEOM = "Korvausoikeuden edellytykset periruisestä suoritetaan viivytyksettä"
# TWO independent vision reads (different render scale) — each covers the body plus the
# page's running header / heading, and BOTH read the intended word "perimisestä".
_GB_VIS_1 = "HE 91/1998 vp 2 § Korvausoikeuden edellytykset perimisestä suoritetaan viivytyksettä"
_GB_VIS_2 = "2 § Korvausoikeuden edellytykset perimisestä suoritetaan viivytyksettä jäljempänä"


def test_multichar_two_agreeing_reads_substitutes_toward_pixels() -> None:
    """(a) TWO independent reads AGREE on the multi-char token → substitute to the consensus."""
    res = reconcile_vision_tokens(_GB_GEOM, _GB_VIS_1, vision_text_2=_GB_VIS_2)
    assert res.changed
    assert [(s.geom_token, s.vision_token) for s in res.substitutions] == [
        ("periruisestä", "perimisestä")
    ]
    assert res.repaired_text == _GB_GEOM.replace("periruisestä", "perimisestä")


def test_multichar_single_read_never_substitutes() -> None:
    """(d) ONE witness only (``vision_text_2=None``) → Gate B never fires (single-letter only)."""
    res = reconcile_vision_tokens(_GB_GEOM, _GB_VIS_1)
    assert not res.changed
    assert res.repaired_text == _GB_GEOM


def test_multichar_reads_disagreeing_with_each_other_is_uncertain_noop() -> None:
    """(b) The two reads DISAGREE on the replacement → uncertain → NO substitution.

    Consensus is the whole non-masking mechanism: if two independent reads do not agree on
    what the pixels show, the garbled token is left byte-identical (never a lone-read guess).
    """
    vis_2_other = "2 § Korvausoikeuden edellytykset perinnöstä suoritetaan viivytyksettä"
    res = reconcile_vision_tokens(_GB_GEOM, _GB_VIS_1, vision_text_2=vis_2_other)
    assert not res.changed
    assert res.repaired_text == _GB_GEOM


def test_multichar_agreement_with_geom_is_byte_identical() -> None:
    """(e) Where both reads AGREE WITH THE GEOM token, nothing is a candidate → unchanged."""
    both = "2 § Korvausoikeuden edellytykset periruisestä suoritetaan viivytyksettä jäljempänä"
    res = reconcile_vision_tokens(_GB_GEOM, both, vision_text_2=both)
    assert not res.changed
    assert res.repaired_text == _GB_GEOM


def test_multichar_substitutes_toward_pixels_not_toward_a_reference() -> None:
    """(c-core) NON-MASKING: the consensus is what the PIXELS show, which may be ≠ any reference.

    Both reads show P="perimiseksi" (the PDF's genuine content); the geom holds a corrupt read
    "periruiseksi". Reconciliation substitutes toward P — NOT toward some other word — so a
    later equality check against a reference X≠P still sees the genuine difference (proved
    end-to-end at the ``compare_he`` layer). Here we assert the substitution lands on the
    pixel consensus P and nothing else.
    """
    geom = "Korvausoikeuden edellytykset periruiseksi suoritetaan viivytyksettä"
    p = "perimiseksi"
    vis1 = f"2 § Korvausoikeuden edellytykset {p} suoritetaan viivytyksettä"
    vis2 = f"Korvausoikeuden edellytykset {p} suoritetaan viivytyksettä jäljempänä"
    res = reconcile_vision_tokens(geom, vis1, vision_text_2=vis2)
    assert res.changed
    assert res.repaired_text == geom.replace("periruiseksi", p)  # toward the pixels (P)


def test_multichar_wholesale_different_word_rejected_by_similarity_bound() -> None:
    """A genuinely DIFFERENT token (low char-similarity) is NOT adopted even with two agreeing reads.

    The conservative similarity guard keeps a wholesale-different word (keskus vs yhdistys —
    ratio ≈ 0.68 < 0.75) from being rewritten. (Substituting toward a true pixel consensus is
    non-masking regardless; this bound just refuses wholesale token replacement so the recovery
    stays a corrupt READ of the same word.)
    """
    geom = "tekee Potilasvakuutuskeskus asiassa"
    diff = "tekee potilasvakuutusyhdistys asiassa"  # a DIFFERENT word, both reads agree on it
    res = reconcile_vision_tokens(geom, diff, vision_text_2=diff)
    assert not res.changed
    assert res.repaired_text == geom
