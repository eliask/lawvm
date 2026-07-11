"""Tests for the op-equivalence quotient (``finland.op_equivalence``).

The relation folds ONLY the unarguably-inert invisible/whitespace layer and emits
everything else as a typed residual. These tests pin both halves: the inert folds
collapse (soft-hyphen joins, Cf format chars, whitespace), and — deliberately —
VISIBLE glyph differences (dashes) do NOT collapse but fall through as residuals, so a
genuine numeric/citation difference can never hide inside a fold.
"""
from __future__ import annotations

import pytest

from lawvm.finland.op_equivalence import EncodingFold, text_equivalence
from lawvm.ingest.llm_backends.prompt_fingerprint import prompt_fingerprint


def test_identical_text_is_equal_with_no_folds():
    v = text_equivalence("3 § muutetaan", "3 § muutetaan")
    assert v.equal and not v.residual
    assert v.folds == ()  # clean payload → output-sparse audit trail


def test_soft_hyphen_line_join_folds():
    # discretionary soft hyphen (U+00AD) at a line break → fused word
    v = text_equivalence("kriisinrat­\nkaisusta", "kriisinratkaisusta")
    assert v.equal
    assert EncodingFold.SOFT_HYPHEN_JOIN in v.folds


def test_cf_format_char_deleted():
    # ZERO WIDTH SPACE (U+200B, category Cf) is invisible → deleted
    v = text_equivalence("sana​toinen", "sanatoinen")
    assert v.equal
    assert EncodingFold.CF_FORMAT in v.folds


def test_nbsp_and_whitespace_collapse():
    # NBSP (Zs) → space, then run/newline collapse + trim
    v = text_equivalence("3 §   muutetaan\n", "3 § muutetaan")
    assert v.equal
    assert EncodingFold.WHITESPACE in v.folds


def test_intra_cell_newline_folds_to_space():
    # A multi-line CELL: the wrapped Docling read has a newline where the witness has a space;
    # the WHITESPACE fold (\s runs, newline included) makes them compare equal.
    v = text_equivalence("Sahkon\nkulutus", "Sahkon kulutus")
    assert v.equal
    assert EncodingFold.WHITESPACE in v.folds


def test_real_hyphen_at_line_break_dehyphenates():
    # A REAL hyphen falling at a wrapped line break ("veroluok-\nka") is fused, mirroring the
    # soft-hyphen join - the same de-hyphenation the reconciled cell witness relies on.
    v = text_equivalence("veroluok-\nka", "veroluokka")
    assert v.equal
    assert EncodingFold.SOFT_HYPHEN_JOIN in v.folds


def test_control_char_noise_from_broken_cmap_is_stripped():
    # A broken ToUnicode CMap sprays C0/C1 control bytes into the text layer; those are pure
    # noise (category Cc) and are DELETED, so the witnessed cell compares equal to Docling.
    v = text_equivalence("Nimi\x01\x1f ja\x7f numero\x9f", "Nimi ja numero")
    assert v.equal
    assert EncodingFold.CONTROL_STRIP in v.folds


def test_control_strip_cannot_hide_a_genuine_difference():
    # The control-strip is inert: deleting only control codepoints can never mask a real
    # digit/letter difference - a changed figure still falls through as a residual.
    v = text_equivalence("numero\x01 5,9", "numero 6,5")
    assert not v.equal and v.residual


def test_genuine_numeric_difference_is_a_residual():
    v = text_equivalence("veroprosentti 5,9", "veroprosentti 6,5")
    assert not v.equal and v.residual
    # the residual carries the canonical forms for adjudication
    assert v.left_canon == "veroprosentti 5,9"
    assert v.right_canon == "veroprosentti 6,5"


def test_single_dash_glyph_difference_is_not_folded_survives_as_residual():
    # en-dash vs em-dash (and every dash-family glyph): the GLYPH IDENTITY of a SINGLE dash is
    # a VISIBLE difference that is deliberately NOT speculatively folded. Exactness, not slop:
    # it survives as a residual for the discovery loop (T1 adjudication → image tier) to judge,
    # never silently declared equal.
    v = text_equivalence("16 a–b", "16 a—b")
    assert not v.equal and v.residual
    # the dash is PRESENT in both canon forms (never deleted) — only its glyph differs, and
    # that visible difference is preserved for adjudication, not folded away.
    assert v.left_canon == "16 a–b" and v.right_canon == "16 a—b"


def test_separator_dash_run_is_folded():
    # A run of 2+ dashes ("— — —") is an inert statute rule / elision marker the text
    # layer captures but the clean XML body omits — folded so the bodies compare equal.
    v = text_equivalence(
        "Uskotun miehen palkkio maksetaan.",
        "Uskotun miehen palkkio maksetaan. — — — — — —",
    )
    assert v.equal
    assert EncodingFold.SEPARATOR_DASH_RUN in v.folds


def test_dot_leader_run_is_folded_decimal_preserved():
    # a run of 2+ dots is a table/TOC leader (inert) → folded; a SINGLE dot is a decimal
    # point and stays substantive so a genuine number difference is not hidden.
    v = text_equivalence("Käsivarsi 2,46", "Käsivarsi.................. 2,46")
    assert v.equal
    assert EncodingFold.DOT_LEADER in v.folds
    v2 = text_equivalence("vero 2.46", "vero 2.99")
    assert not v2.equal and EncodingFold.DOT_LEADER not in v2.folds


def test_single_dash_is_not_swept_by_the_run_fold():
    # The {2,}-dash requirement keeps a SINGLE dash from being SWEPT (deleted) by the run
    # fold. Its glyph is NOT folded either, so an em/en difference on a lone dash falls through
    # as a residual — but the dash's PRESENCE is preserved (the run fold does not fire, words
    # are not merged away).
    v = text_equivalence("veroluokka 5—10", "veroluokka 5–10")
    assert not v.equal and v.residual  # same range, but the visible dash glyph differs
    assert EncodingFold.SEPARATOR_DASH_RUN not in v.folds  # single dash: not a run, not deleted
    # the dash is preserved in the canon (present, never merged): "a—b" stays "a—b", never "ab"
    solo = text_equivalence("a—b", "a—b")
    assert solo.equal and solo.left_canon == "a—b"


def test_numeric_difference_across_a_range_is_a_residual():
    # A genuine numeric difference across a range separator is a residual regardless of the
    # dash glyph — there is no dash-glyph fold at all, so a changed figure always survives.
    v = text_equivalence("momentin 5—10 kohta", "momentin 5—11 kohta")
    assert not v.equal and v.residual  # 10 != 11
    v2 = text_equivalence("60―62 §", "60―63 §")
    assert not v2.equal and v2.residual  # 62 != 63 (same horizontal-bar glyph, only digits differ)


def test_folds_are_deterministic_and_sorted():
    v = text_equivalence("a​ b­\nc", "a bc")
    assert v.equal
    assert list(v.folds) == sorted(v.folds)


def test_empty_inputs_are_equal_never_raise():
    assert text_equivalence("", "").equal
    assert text_equivalence("   \n ", "").equal  # pure-whitespace canonicalises to ""


# ---------------------------------------------------------------------------
# WHITESPACE_PUNCT — whitespace adjacent to punctuation (fold #3)
# ---------------------------------------------------------------------------


def test_space_before_colon_folds():
    # "9 § :n" vs "9 §:n": a space before the ":n" genitive suffix is a typesetting artifact.
    v = text_equivalence("muutetaan 9 § :n 1 momentti", "muutetaan 9 §:n 1 momentti")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds


def test_spaces_inside_parens_around_slash_fold():
    # "( / )" vs "(/)": spaces inside the parens (around a slash) are inert typesetting.
    v = text_equivalence("kohta ( / ) korvataan", "kohta (/) korvataan")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds
    # all interior-space variants collapse to the same canonical "(/)"
    for variant in ("(/ )", "( /)", "( / )"):
        assert text_equivalence("(/)", variant).equal


def test_space_before_period_folds():
    # "20 ." vs "20.": a space before a period is inert (the period is still PRESENT on both).
    v = text_equivalence("annetun lain 20 .", "annetun lain 20.")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT in v.folds


def test_thin_space_before_section_sign_is_already_folded_by_whitespace():
    # The convicted "2 §:n" residual differs ONLY by a THIN SPACE U+2009 (vs an ordinary
    # space) before "§". U+2009 is in ZS_NON_ASCII_SPACE_CPS, so the pre-existing WHITESPACE
    # fold (Zs→space + run-collapse) already equalises it — no §-specific handling is needed.
    v = text_equivalence("2 §:n mukaan", "2 §:n mukaan")  # left "§" preceded by U+2009
    assert v.equal
    assert EncodingFold.WHITESPACE in v.folds
    # U+202F NARROW NO-BREAK SPACE and U+00A0 NBSP fold the same way (all in the Zs table).
    for zs in (" ", " "):
        assert text_equivalence(f"2{zs}§:n mukaan", "2 §:n mukaan").equal


def test_space_before_section_sign_is_deliberately_not_folded():
    # "§" is EXCLUDED from the before-set: the standard "N §" reference legitimately carries
    # a space, so WHITESPACE_PUNCT must NOT strip it (that would fire on almost every body and
    # recover no equivalences). "3 § muutetaan" stays byte-clean → no punct fold recorded.
    v = text_equivalence("3 § muutetaan", "3 § muutetaan")
    assert v.equal and v.folds == ()
    # a genuine section-number difference is of course still a residual
    assert text_equivalence("3 § muutetaan", "4 § muutetaan").residual


def test_whitespace_punct_recorded_only_when_it_fires():
    # a clean payload with no punctuation-adjacent space does not record the fold
    v = text_equivalence("3 §:n 1 momentti", "3 §:n 1 momentti")
    assert v.equal
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_whitespace_punct_does_not_hide_numeric_difference():
    # WHITESPACE_PUNCT removes ONLY spaces next to punctuation — digits are untouched, so a
    # genuine numeric difference across a comma/decimal is never hidden.
    v = text_equivalence("veroprosentti 5,9", "veroprosentti 5,10")
    assert not v.equal and v.residual  # 9 != 10
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_whitespace_punct_does_not_hide_word_difference():
    # a dropped word is content, not typesetting — stays a residual.
    v = text_equivalence("veroviraston tai kunnan", "veroviraston kunnan")
    assert not v.equal and v.residual


def test_whitespace_punct_does_not_hide_citation_difference():
    # a citation year difference survives — WHITESPACE_PUNCT touches no digit or the "/".
    v = text_equivalence("annetun lain (768/2005)", "annetun lain (768/2006)")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_terminal_period_presence_stays_a_residual():
    # WHITESPACE_PUNCT folds whitespace AROUND punctuation, NEVER a terminal period's
    # PRESENCE — a trailing period can be load-bearing, so it stays a residual.
    v = text_equivalence("maksetaan markkaa.", "maksetaan markkaa")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_PUNCT not in v.folds


def test_midword_space_split_folds():
    # pdfium kerning artifact: a stray space splits one word ("alueeseen" → "alue eseen").
    # Deleting the letter-letter space recovers the equality.
    v = text_equivalence("yhteiseen alueeseen olevan", "yhteiseen alue eseen olevan")
    assert v.equal
    assert EncodingFold.WHITESPACE_MIDWORD in v.folds


def test_midword_space_leading_capital_split_folds():
    # the dominant form: a leading capital detaches ("Verotuksen" → "V erotuksen").
    v = text_equivalence("Verotuksen oikaisulautakunta", "V erotuksen oikaisulautakunta")
    assert v.equal
    assert EncodingFold.WHITESPACE_MIDWORD in v.folds


def test_midword_space_merge_folds_symmetrically():
    # the inverse under-space merges two words ("tai siihen" → "taisiihen"); the fold is
    # applied to BOTH sides so a merge and a split of the same letters both reach equality.
    v = text_equivalence("koskevien tai siihen liittyvien", "koskevien taisiihen liittyvien")
    assert v.equal
    assert EncodingFold.WHITESPACE_MIDWORD in v.folds


def test_midword_space_recorded_only_when_it_rescues_equality():
    # output-sparse: a clean payload with ordinary inter-word spaces does NOT record the fold
    # (the fold is attempted only when the texts still differ after every other fold).
    v = text_equivalence("laki tulee voimaan", "laki tulee voimaan")
    assert v.equal
    assert EncodingFold.WHITESPACE_MIDWORD not in v.folds


def test_midword_space_does_not_fold_thousands_separator():
    # a digit-flanked space is a thousands separator, NOT a mid-word artifact: "2 500" must
    # stay a residual against "2500" (the seam is digit-space-digit, never touched).
    v = text_equivalence("enintään 2 500 euroa", "enintään 2500 euroa")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_MIDWORD not in v.folds


def test_midword_space_does_not_fold_section_number_boundary():
    # "4 a" (section 4 a) is digit-space-letter — not letter-letter — so the space stays; a
    # genuine "5 §" reference is likewise untouched.
    v = text_equivalence("4 a §:n nojalla", "4a §:n nojalla")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_MIDWORD not in v.folds


def test_midword_space_does_not_hide_glyph_or_letter_difference():
    # deleting ONLY the space can never hide a differing letter: "alue eseen" vs "alue asten"
    # despace to "alueeseen" vs "alueasten" — still divergent.
    v = text_equivalence("yhteiseen alue eseen", "yhteiseen alue asten")
    assert not v.equal and v.residual


def test_midword_space_genuine_word_boundary_is_the_documented_tradeoff():
    # DOCUMENTED accepted trade: a genuine word-boundary difference among the SAME letters
    # ("työn antaja" vs "työnantaja") DOES fold — same letters, same order. The adjudicator
    # guard finds no such pair in the genuine-difference corpus, so it masks no real amendment.
    v = text_equivalence("työn antaja maksaa", "työnantaja maksaa")
    assert v.equal
    assert EncodingFold.WHITESPACE_MIDWORD in v.folds


# ---------------------------------------------------------------------------
# WHITESPACE_SEP — whitespace adjacent to a range/citation separator (fold #6)
# ---------------------------------------------------------------------------


def test_ws_sep_space_around_citation_slash_folds():
    # "37 /1895" vs "37/1895": a space inside a statute-number citation slash is inert.
    v = text_equivalence("ulosottolaissa (37/1895)", "ulosottolaissa (37 /1895)")
    assert v.equal
    assert EncodingFold.WHITESPACE_SEP in v.folds


def test_ws_sep_space_around_range_dash_folds():
    # "195— 196" vs "195—196": a space next to a numeric-range dash (em, en, and the other
    # typographic dashes U+2012–2015) is typesetting, not content — the dash itself survives.
    for xml, pdf in (
        ("pykälissä 195—196", "pykälissä 195— 196"),     # em-dash
        ("vuosina 2014–2020", "vuosina 2014– 2020"),     # en-dash
        ("ax–maakunta", "ax– maakunta"),                 # en-dash before a letter
    ):
        v = text_equivalence(xml, pdf)
        assert v.equal, (xml, pdf)
        assert EncodingFold.WHITESPACE_SEP in v.folds


def test_ws_sep_section_number_enumeration_comma_folds():
    # "169, 209" vs "169,209": a space after a comma BETWEEN DIGITS is a section-number
    # enumeration artifact — folded. A PROSE comma ("virasto, joka") is NOT (letter follows).
    v = text_equivalence("Mitä 169,209 säädetään", "Mitä 169, 209 säädetään")
    assert v.equal
    assert EncodingFold.WHITESPACE_SEP in v.folds
    prose = text_equivalence("virasto, joka päättää", "virasto,joka päättää")
    assert not prose.equal and prose.residual  # prose comma untouched → residual
    assert EncodingFold.WHITESPACE_SEP not in prose.folds


def test_ws_sep_clitic_colon_after_section_or_digit_folds():
    # "13 §: ssä" vs "13 §:ssä": a space after a CLITIC colon (colon following a §/number)
    # is inert. A PROSE colon ("otsikko: teksti") is NOT folded.
    v = text_equivalence("13 §:ssä säädetään", "13 §: ssä säädetään")
    assert v.equal
    assert EncodingFold.WHITESPACE_SEP in v.folds
    prose = text_equivalence("otsikko:teksti", "otsikko: teksti")
    assert not prose.equal and prose.residual
    assert EncodingFold.WHITESPACE_SEP not in prose.folds


def test_ws_sep_list_marker_paren_folds():
    # "3) maatalous" vs "3)maatalous": a space after a numbered list-marker paren is inert.
    v = text_equivalence("kohdassa;3)maataloustuki", "kohdassa;3) maataloustuki")
    assert v.equal
    assert EncodingFold.WHITESPACE_SEP in v.folds


def test_ws_sep_recorded_only_when_it_fires():
    # output-sparse: a clean payload with no separator-adjacent space does not record the fold.
    v = text_equivalence("Mitä 169,209 säädetään", "Mitä 169,209 säädetään")
    assert v.equal
    assert EncodingFold.WHITESPACE_SEP not in v.folds


# --- WHITESPACE_SEP mutation guards: it must NOT mask a real content/glyph/word difference ---


def test_ws_sep_does_not_hide_numeric_difference_across_a_range_dash():
    # deleting the space around a dash preserves the skeleton, so a changed range figure
    # ("195—196" vs "195—197") still falls through as a residual.
    v = text_equivalence("pykälissä 195—196", "pykälissä 195— 197")
    assert not v.equal and v.residual  # 196 != 197
    w = text_equivalence("vuosina 2014–2020", "vuosina 2015– 2020")
    assert not w.equal and w.residual  # 2014 != 2015


def test_ws_sep_does_not_fold_the_dash_glyph_identity():
    # WHITESPACE_SEP removes only the SPACE, never the dash — so an en-dash-vs-em-dash
    # difference on a lone dash is still the visible residual the discovery loop must judge.
    v = text_equivalence("16 a–b", "16 a—b")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_SEP not in v.folds  # no space to fold; glyph difference survives


def test_ws_sep_excludes_ascii_hyphen_compound_word_boundary():
    # the ASCII hyphen "-" is a compound-word joiner ("sotilas- ja siviili…"): the space after
    # it is a legitimate word boundary and must NOT be folded (only the typographic dashes are).
    v = text_equivalence("sotilas-ja siviilihenkilöstö", "sotilas- ja siviilihenkilöstö")
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_SEP not in v.folds


def test_ws_sep_does_not_fold_thousands_separator_or_section_sublabel():
    # a digit-space-digit thousands separator ("2 500" vs "2500") and a digit-space-letter
    # section sub-label ("4 a" vs "4a") have NO separator flank → untouched, both residuals.
    assert text_equivalence("enintään 2 500 euroa", "enintään 2500 euroa").residual
    assert text_equivalence("4 a §:n nojalla", "4a §:n nojalla").residual


def test_ws_sep_does_not_hide_a_dropped_word():
    # a dropped word is content, not typesetting — stays a residual even with separators present.
    v = text_equivalence("mitä 195—196 §:ssä veroviraston tai kunnan",
                         "mitä 195— 196 §:ssä veroviraston kunnan")
    assert not v.equal and v.residual


def test_ws_sep_leaves_he_266_2002_word_split_as_a_divergence():
    # LIVE-DATA mutation guard (HE 266/2002, target 1501/1993/173a/2): the payload differs by
    # (a) a comma-enumeration space (WHITESPACE_SEP folds it), (b) a space BEFORE "§" (NOT
    # folded — § is excluded), and (c) a genuine pdfium word-split "säädetään"→"sääde tään".
    # Because the § seam is left intact, the two witnesses still differ after WHITESPACE_SEP,
    # so the output-sparse WHITESPACE_MIDWORD fallback never fires and the word-split correctly
    # SURVIVES as a residual — it must NOT be silently folded to exact.
    xml = ("Mitä 169,209 ja 209 g—209 i§:ssä säädetään verovelvollisen velvollisuuksista, "
           "sovelletaan myös 1 momentissa tarkoitettuun edustajaan.")
    pdf = ("Mitä 169, 209 ja 209 g—209 i §:ssä sääde tään verovelvollisen velvollisuuksista, "
           "sovelletaan myös 1 momentissa tarkoitettuun edustajaan.")
    v = text_equivalence(xml, pdf)
    assert not v.equal and v.residual
    assert EncodingFold.WHITESPACE_MIDWORD not in v.folds  # the word-split is NOT masked


# ===========================================================================
# GOVERNED-INVENTORY ENFORCEMENT (audit fix #6-fingerprint)
#
# Every fold claims a class of typography is LEGALLY INERT — two payloads equal
# modulo it count as "exact". A fold that over-reaches would MASK a real content
# difference, the exact "slop" this quotient exists to prevent. Ad-hoc per-fold
# tests are not enough: nothing GUARANTEES that every fold carries both a
# fires-on-inert proof and a non-masking proof, and nothing alerts when the fold
# set changes. These two mechanisms close that gap:
#
#   (A) an inventory FINGERPRINT ratchet — the frozen member set is content-hashed
#       and pinned to a checked-in constant; adding/removing/renaming a fold reddens
#       CI with the update protocol (mirrors the classifier-wrap / regex ratchets).
#   (B) a totality-enforced per-fold mutation CONTRACT table — every fold MUST carry
#       BOTH a fires-on-inert proof AND a non-masking proof, or CI reddens; you cannot
#       add a fold without a non-masking guard.
#
# The MECHANISM is jurisdiction-neutral: it imports only the enum and the shared
# ``prompt_fingerprint`` primitive; nothing FI-specific is baked into the scaffolding
# beyond the enum's own members (op_equivalence merely happens to live under finland/).
# It is an INVENTORY GATE, not a runtime cache key — the phase-2 harness recomputes
# equality verdicts live, so the fingerprint is never folded into any stored key.
# ===========================================================================

# Frozen fingerprint of the EncodingFold inventory (sorted "NAME=value" member pairs),
# computed with the shared determinism-firewall primitive so this ratchet re-keys on the
# SAME content-hash discipline as the LLM cache keys. Update ONLY via the protocol in the
# failure message below (ship the two new proofs FIRST, then rebaseline this constant).
FOLD_SET_FINGERPRINT = "ead044f16c6cc5c0"


def _fold_inventory_vocab() -> list[str]:
    """The content-addressed inventory: sorted "NAME=value" strings, one per fold member.

    Both the member NAME and its string VALUE are hashed, so a rename, an added member,
    a removed member, or a changed value all move the fingerprint.
    """
    return sorted(f"{m.name}={m.value}" for m in EncodingFold)


def test_fold_set_inventory_fingerprint_is_frozen():
    # (A) Inventory-fingerprint ratchet. The frozen fold set is the quotient's whole
    # attack surface; pin it so no new fold can silently land.
    got = prompt_fingerprint(vocab=_fold_inventory_vocab())
    assert got == FOLD_SET_FINGERPRINT, (
        "the inert-fold set changed; a new fold must ship with a fires-on-inert test "
        "AND a non-masking mutation guard (register it in FOLD_CONTRACTS — the totality "
        "meta-test enforces both), and ONLY THEN update FOLD_SET_FINGERPRINT.\n"
        f"  expected {FOLD_SET_FINGERPRINT!r}\n"
        f"  computed {got!r}\n"
        f"  current inventory: {_fold_inventory_vocab()}"
    )


# FOLD_CONTRACTS: for EVERY EncodingFold member, a 4-tuple
#     (inert_left, inert_right, mutated_left, mutated_right)
# proving BOTH halves of the fold's contract:
#   (1) POSITIVE  — the INERT pair is EQUAL after folding AND this fold is CREDITED
#       (it actually fired to rescue equality on its inert target); and
#   (2) NON-MASKING — a GENUINE content difference (a digit / glyph / word / citation
#       change) injected in the SAME structural position the fold operates on SURVIVES
#       as a residual (the mutated pair is NOT equal), so the fold cannot hide it.
# The meta-test asserts the keys are TOTAL over EncodingFold, so a new fold cannot land
# without a non-masking proof. These rows mirror the ad-hoc guards above in one table.
FOLD_CONTRACTS: dict[EncodingFold, tuple[str, str, str, str]] = {
    # soft-hyphen line join fuses the two halves; a changed fused letter (a→o) survives.
    EncodingFold.SOFT_HYPHEN_JOIN: (
        "kriisinrat­\nkaisusta", "kriisinratkaisusta",
        "kriisinrat­\nkaisusta", "kriisinratkoisusta",
    ),
    # invisible ZERO WIDTH SPACE deleted; a changed letter in the same word survives.
    EncodingFold.CF_FORMAT: (
        "sana​toinen", "sanatoinen",
        "sana​toinen", "sanatuinen",
    ),
    # C0/C1 control noise deleted; a changed figure past the control byte survives.
    EncodingFold.CONTROL_STRIP: (
        "Nimi\x01\x1f ja\x7f numero\x9f", "Nimi ja numero",
        "numero\x01 5,9", "numero 6,5",
    ),
    # whitespace run/newline collapse; a changed word in the same slot survives.
    EncodingFold.WHITESPACE: (
        "3 §   muutetaan\n", "3 § muutetaan",
        "3 §   muutetaan\n", "3 § korotetaan",
    ),
    # a run of 2+ dashes ("— — —") deleted; a genuine trailing word is NOT swept.
    EncodingFold.SEPARATOR_DASH_RUN: (
        "Uskotun miehen palkkio maksetaan.",
        "Uskotun miehen palkkio maksetaan. — — — — — —",
        "Uskotun miehen palkkio maksetaan valtiolle.",
        "Uskotun miehen palkkio maksetaan. — — — — — —",
    ),
    # a run of 2+ dots (TOC/table leader) deleted; the trailing figure difference survives.
    EncodingFold.DOT_LEADER: (
        "Käsivarsi 2,46", "Käsivarsi.................. 2,46",
        "Käsivarsi 2,46", "Käsivarsi.................. 2,99",
    ),
    # a space before a period folded; a changed number at that punctuation seam survives.
    EncodingFold.WHITESPACE_PUNCT: (
        "annetun lain 20 .", "annetun lain 20.",
        "annetun lain 20 .", "annetun lain 21.",
    ),
    # a letter-letter mid-word space deleted; a changed letter in that word survives.
    EncodingFold.WHITESPACE_MIDWORD: (
        "yhteiseen alueeseen olevan", "yhteiseen alue eseen olevan",
        "yhteiseen alue eseen olevan", "yhteiseen alue asten olevan",
    ),
    # a space inside a citation slash folded; a changed range figure at a dash survives.
    EncodingFold.WHITESPACE_SEP: (
        "ulosottolaissa (37/1895)", "ulosottolaissa (37 /1895)",
        "pykälissä 195—196", "pykälissä 195— 197",
    ),
}


def test_fold_contracts_are_total_over_the_enum():
    # (B.i) TOTALITY: a new fold with no contract FAILS here — you cannot add a fold
    # without both a fires-on-inert proof and a non-masking proof registered below.
    assert set(FOLD_CONTRACTS) == set(EncodingFold), (
        "FOLD_CONTRACTS must cover EVERY EncodingFold member; each fold needs BOTH a "
        "fires-on-inert row and a non-masking mutation guard.\n"
        f"  missing contracts: {sorted(f.name for f in set(EncodingFold) - set(FOLD_CONTRACTS))}\n"
        f"  stale contracts:   {sorted(f.name for f in set(FOLD_CONTRACTS) - set(EncodingFold))}"
    )


@pytest.mark.parametrize("fold", list(EncodingFold), ids=lambda f: f.name)
def test_every_fold_fires_on_inert_and_cannot_mask_a_content_difference(fold: EncodingFold):
    # (B.ii) Per-fold, both halves of the contract. Parametrising over the LIVE enum means
    # a newly-added fold with no FOLD_CONTRACTS row fails the lookup below immediately.
    assert fold in FOLD_CONTRACTS, (
        f"EncodingFold.{fold.name} has no FOLD_CONTRACTS row: every fold must register "
        "BOTH a fires-on-inert proof AND a non-masking mutation guard."
    )
    inert_left, inert_right, mut_left, mut_right = FOLD_CONTRACTS[fold]

    inert = text_equivalence(inert_left, inert_right)
    assert inert.equal, f"{fold.name}: the inert pair must be EQUAL after folding"
    assert fold in inert.folds, (
        f"{fold.name}: the inert pair must CREDIT this fold (it must actually fire to "
        f"rescue equality), but recorded folds were {[f.name for f in inert.folds]}"
    )

    mutated = text_equivalence(mut_left, mut_right)
    assert not mutated.equal and mutated.residual, (
        f"{fold.name}: a GENUINE content difference in the fold's structural position "
        "MUST survive as a residual — the fold must never mask it."
    )
