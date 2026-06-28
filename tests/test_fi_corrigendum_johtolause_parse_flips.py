"""Regression: johtolause corrigenda flip a declining parse into a parsing one.

Each manual johtolause corrigendum in ``data/finland/source_defect_fixes_fi.yaml``
exists because a source-text defect (a glued pykälä token like ``18§``, a stray
period like ``uusi.3``, or a dittographic ``uusi momentti 3 momentti``) makes the
new grammar parser drop the affected insertion target.  Applying the corrigendum
restores the target.

These tests embed the verbatim source johtolause text (whitespace-normalized to
the single-spaced form the clause parser receives) and apply the corrigendum's
own ``wrong_text -> correct_text`` replacements pulled from the live patch table
-- mirroring how ``patch_source_xml`` rewrites the johtolause byte range before
re-parsing.  The malformed text must drop the target; the corrected text must
recover it.  This pins the decline->parse flip so the corrigenda cannot silently
stop mattering (e.g. if the parser later starts tolerating the defect, or the
manual entries drift).
"""

from __future__ import annotations

import pytest

from lawvm.finland import corrigendum as corr
from lawvm.finland.johtolause.api import parse_clause


# Each case pins:
#   amendment_id : the SID as it appears in source_defect_fixes_fi.yaml (NUM/YEAR)
#   wrong_text   : the verbatim johtolause as it appears in the source XML, with
#                  the multi-line indentation collapsed to single spaces (the
#                  form the clause parser receives after tokenization).
#   unlocked     : an op code that the corrigendum recovers -- absent from the
#                  malformed parse, present after the corrigendum is applied.
_CORRIGENDUM_PARSE_FLIPS = [
    # Family A -- glued pykala symbol ("N§") in the "kumotun N§:n tilalle uusi
    # nain kuuluva N§:" reinsertion arm.  The glued token breaks the whole arm,
    # so the malformed clause parses to nothing.
    {
        "amendment_id": "383/1988",
        "wrong_text": (
            "lisätään 31 päivänä lokakuuta 1896 annettuun ulosottoasetukseen "
            "siitä 8 päivänä helmikuuta 1985 annetulla asetuksella (162/85) "
            "kumotun 18§:n tilalle uusi näin kuuluva 18§:"
        ),
        "unlocked": "L P 18",
    },
    {
        "amendment_id": "990/1988",
        "wrong_text": (
            "lisätään lapsen hoitotuesta 10 päivänä lokakuuta 1969 annettuun "
            "asetukseen (632/69) siitä 1 päivänä helmikuuta 1974 annetulla "
            "asetuksella (114/74) kumotun 5§:n tilalle uusi näin kuuluva 5§:"
        ),
        "unlocked": "L P 5",
    },
    {
        "amendment_id": "1027/1989",
        "wrong_text": (
            "lisätään tapaturmavirastosta 1 päivänä heinäkuuta 1965 annettuun "
            "lakiin (389/65) siitä 6 päivänä marraskuuta 1987 annetulla lailla "
            "(819/87) kumotun 2§:n tilalle uusi näin kuuluva 2§:"
        ),
        "unlocked": "L P 2",
    },
    {
        "amendment_id": "23/1989",
        "wrong_text": (
            "lisätään valtion maatalouskemian laitoksesta 10 päivänä elokuuta "
            "1979 annettuun lakiin (652/79) siitä 15 päivänä huhtikuuta 1988 "
            "annetulla lailla (323/88) kumotun 2§:n tilalle uusi näin kuuluva 2§:"
        ),
        "unlocked": "L P 2",
    },
    {
        "amendment_id": "24/1989",
        "wrong_text": (
            "lisätään valtion siementarkastuslaitoksesta 10 päivänä elokuuta "
            "1979 annettuun lakiin (649/79) siitä 15 päivänä huhtikuuta 1988 "
            "annetulla lailla (324/88) kumotun 2§:n tilalle uusi näin kuuluva 2§:"
        ),
        "unlocked": "L P 2",
    },
    {
        "amendment_id": "25/1989",
        "wrong_text": (
            "lisätään valtion maitovalmisteiden tarkastuslaitoksesta 10 päivänä "
            "elokuuta 1979 annettuun lakiin (647/79) siitä 15 päivänä huhtikuuta "
            "1988 annetulla lailla (325/88) kumotun 2§:n tilalle uusi näin "
            "kuuluva 2§:"
        ),
        "unlocked": "L P 2",
    },
    {
        "amendment_id": "703/1989",
        "wrong_text": (
            "lisätään ammatinvalinnanohjauksesta 23 päivänä joulukuuta 1964 "
            "annettuun asetukseen (632/64) siitä 2 päivänä helmikuuta 1973 "
            "annetulla asetuksella (82/73) kumotun 20§:n tilalle uusi näin "
            "kuuluva 20§:"
        ),
        "unlocked": "L P 20",
    },
    # Family B -- stray period glues "uusi" to the momentti number ("uusi.3").
    # The malformed insertion target is dropped (the parse stops at the prior
    # "53 §:n 2 momenttiin uusi 5 a kohta" insert); the corrigendum recovers the
    # "78 §:ään uusi 3 momentti" insert (and the downstream inserts with it).
    {
        "amendment_id": "268/1978",
        "wrong_text": (
            "kumotaan 26 päivänä kesäkuuta 1959 annetun rakennusasetuksen "
            "(266/59) 9 § ja 79 §:n 1 momentti, muutetaan 3 ja 10 §, 11 §:n 1 "
            "momentti, 12 §, 65 §:n 1 momentti, 124 §:n 1 momentti, 125 §:n 2 "
            "momentin 10 kohta ja 138 §, lisätään 53 §:n 2 momenttiin uusi 5 a "
            "kohta, 78 §:ään uusi.3 momentti, 88 §:ään, sellaisena kuin se on 31 "
            "päivänä lokakuuta 1973 annetussa asetuksessa (791/73), uusi 5 "
            "momentti ja 125 §:n 2 momenttiin uusi 10 a kohta seuraavasti:"
        ),
        "unlocked": "L P 78 3",
    },
    # Family C -- dittographic "momentti" word ("uusi momentti 3 momentti").
    # The malformed insertion target is dropped; the corrigendum recovers the
    # "10 §:ään uusi 3 momentti" insert.
    {
        "amendment_id": "1195/1998",
        "wrong_text": (
            "muutetaan erikoislääkärin tutkinnosta 4 päivänä syyskuuta 1998 "
            "annetun asetuksen (678/1998) 10 §:n 3 momentti; sekä lisätään 10 "
            "§:ään uusi momentti 3 momentti, jolloin nykyinen 3 momentti siirtyy "
            "4 momentiksi, seuraavasti:"
        ),
        "unlocked": "L P 10 3",
    },
]


def _table_patches_for(amendment_id: str):
    """Pull (wrong_text, correct_text) text patches for an amendment.

    The patch table keys amendments by YEAR/NUM (``mid``); the manual YAML and
    these cases use the NUM/YEAR SID form, so swap the halves.
    """
    num, year = amendment_id.split("/")
    mid = f"{year}/{num}"
    table = corr.get_patch_table()
    pairs = []
    for op in table._patches.get(mid, []):
        patch = op.text_patch
        if patch is None:
            continue
        wrong = patch.selector.match_text
        correct = patch.replacement
        if wrong and correct:
            pairs.append((wrong, correct))
    return pairs


def _apply_text_patches(text: str, pairs) -> str:
    for wrong, correct in pairs:
        text = text.replace(wrong, correct)
    return text


@pytest.mark.parametrize(
    "case",
    _CORRIGENDUM_PARSE_FLIPS,
    ids=[c["amendment_id"].replace("/", "_") for c in _CORRIGENDUM_PARSE_FLIPS],
)
def test_corrigendum_flips_declining_johtolause_into_parsing(case) -> None:
    """The malformed johtolause drops the target; the corrigendum recovers it."""
    amendment_id = case["amendment_id"]
    wrong_text = case["wrong_text"]
    unlocked = case["unlocked"]

    pairs = _table_patches_for(amendment_id)
    assert pairs, f"no manual corrigendum text patches found for {amendment_id}"

    correct_text = _apply_text_patches(wrong_text, pairs)
    assert correct_text != wrong_text, (
        f"corrigendum for {amendment_id} did not rewrite the embedded source "
        f"johtolause -- the wrong_text fixture has drifted from the manual entry"
    )

    num, year = amendment_id.split("/")
    statute_id = f"{year}/{num}"

    before_ops = [op.code() for op in parse_clause(wrong_text, statute_id=statute_id).parsed_ops]
    after_ops = [op.code() for op in parse_clause(correct_text, statute_id=statute_id).parsed_ops]

    # The defect drops the target; the corrigendum recovers it.
    assert unlocked not in before_ops, (
        f"{amendment_id}: expected malformed johtolause to drop {unlocked!r}, "
        f"but it parsed: {before_ops}"
    )
    assert unlocked in after_ops, (
        f"{amendment_id}: corrigendum-applied johtolause must parse {unlocked!r}; "
        f"got {after_ops}"
    )
    # The corrigendum strictly adds coverage -- every op the malformed parse
    # produced must survive the correction (no regressions on the rest).
    for op_code in before_ops:
        assert op_code in after_ops, (
            f"{amendment_id}: corrigendum dropped a previously-parsed op {op_code!r}; "
            f"before={before_ops} after={after_ops}"
        )
