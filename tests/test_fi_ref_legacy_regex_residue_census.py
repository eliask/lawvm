"""Regenerable guard: the legacy plain-text statute-citation REGEX residue is DEAD.

Disposition record (base f5843e95). The production citation lane in
:func:`extract_all_reference_mentions` once kept the legacy regex recognizer
(:func:`extract_plain_text_statute_mentions`) as a typed-residue FALLBACK,
emitting ``phrase_lemma="plain_text_fallback"`` mentions for inline-(id) targets
the construction-grammar lane (:func:`extract_inline_id_construction_mentions`)
did not cover.

A whole-corpus census (59,574 statutes) of that residue found it contributed
EXACTLY 5 mentions, in 5 statutes — and every one was a 2-digit-year citation the
construction lane ALREADY caught with the correct century, MIS-DUPLICATED by the
regex with the WRONG century:

  * The construction lane bounds the 2-digit-year century pivot CAUSALLY by the
    citing statute (a 1993 act citing ``(71/23)`` -> ``1923/71`` — the act given
    in 1923).
  * The regex lane pivots by ``date.today()`` (acausal) -> ``2023/71`` — a
    future-dated, wrong id. Different statute id => different dedup key => the
    regex hit was NOT deduped and surfaced as bogus residue.

So the residue net contributed ZERO citations the construction lane misses
correctly; it was a source of mis-pivoted FALSE edges. It was DELETED, and the
function de-deprecated to an explicitly non-authoritative measurement/audit
recognizer.

This guard pins the disposition two ways:

* Corpus-free witness (always runs): a synthetic 2-digit-year citation in a 1993
  statute. The construction lane resolves it causally; the regex recognizer
  mis-pivots it; and production ``extract_all_reference_mentions`` emits the
  construction's (correct) id and ZERO ``plain_text_fallback``. If a future
  construction-lane regression stops covering this shape, the regex residue does
  NOT silently re-appear — production simply has no such lane — so the loss
  surfaces as a missing-construction-mention failure here, not a silent revert to
  the wrong-pivot fallback.

* Whole-corpus guard (archive-gated, slow): production emits ZERO
  ``plain_text_fallback`` mentions across the entire corpus. Any non-zero count
  means the deleted residue lane was re-introduced.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest

from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
    extract_inline_id_construction_mentions,
    extract_plain_text_statute_mentions,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _akn_body(p_text: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}"><act><body><section><num>1 §</num>'
        f"<paragraph><content><p>{p_text}</p></content></paragraph>"
        "</section></body></act></akomaNtoso>"
    ).encode("utf-8")


# A 1993 statute citing the Nature Conservation Act given in 1923, written with a
# 2-digit year ``(71/23)`` — the exact shape the corpus census found as residue.
_CITING_1993 = "1993/1370"
_BODY_2DIGIT_YEAR = (
    "Täten kumotaan 23 päivänä helmikuuta 1923 annetun "
    "luonnonsuojelulain (71/23) 5 a §."
)


# ---------------------------------------------------------------------------
# Corpus-free witness (always runs) — encodes WHY the residue was dead.
# ---------------------------------------------------------------------------
def test_construction_lane_pivots_two_digit_year_causally() -> None:
    """Construction lane resolves ``(71/23)`` in a 1993 act to ``1923/71``."""
    xml = _akn_body(_BODY_2DIGIT_YEAR)
    constr, _keys = extract_inline_id_construction_mentions(xml, _CITING_1993)
    targets = {
        m.target_provision_ref.statute_id
        for m in constr.mentions
        if m.target_provision_ref is not None
    }
    # Causal pivot: the cited act is the 1923 one, NOT a future-dated 2023 id.
    assert "1923/71" in targets, targets
    assert "2023/71" not in targets, targets


def test_legacy_regex_recognizer_mispivots_two_digit_year() -> None:
    """The legacy regex lane mis-pivots the SAME cite to a wrong (future) century.

    This is the demoted recognizer's documented acausal-pivot defect — exactly
    why its production residue contributed only wrong duplicates. The recognizer
    is retained for measurement/audit only and carries NO reference authority.
    """
    xml = _akn_body(_BODY_2DIGIT_YEAR)
    # Direct call to the (now non-authoritative) recognizer; no deprecation
    # warning should be emitted since it is de-deprecated.
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        res = extract_plain_text_statute_mentions(xml, _CITING_1993)
    targets = {
        m.target_provision_ref.statute_id
        for m in res.mentions
        if m.target_provision_ref is not None
    }
    # The acausal date.today() pivot mints the WRONG century — this is the bogus
    # id the production residue used to emit, distinct from the construction's
    # correct 1923/71, hence undeduped.
    assert "2023/71" in targets, targets


def test_production_emits_no_plain_text_fallback_on_residue_witness() -> None:
    """Production extraction emits the construction's correct id and NO fallback."""
    xml = _akn_body(_BODY_2DIGIT_YEAR)
    res = extract_all_reference_mentions(xml, _CITING_1993)
    fallback = [m for m in res.mentions if m.phrase_lemma == "plain_text_fallback"]
    assert fallback == [], (
        "Production must not emit plain_text_fallback — the legacy regex residue "
        f"lane was deleted. Re-appeared with: {[m.phrase_lemma for m in fallback]}"
    )
    constr_targets = {
        m.target_provision_ref.statute_id
        for m in res.mentions
        if m.target_provision_ref is not None
        and m.phrase_lemma == "citation_construction"
    }
    # The correct (causal) id is present; the wrong-pivot id is absent.
    assert "1923/71" in constr_targets, constr_targets
    assert "2023/71" not in {
        m.target_provision_ref.statute_id
        for m in res.mentions
        if m.target_provision_ref is not None
    }


def test_symbol_is_de_deprecated() -> None:
    """The recognizer is no longer a @deprecated production-fallback symbol."""
    assert (
        getattr(extract_plain_text_statute_mentions, "__deprecated__", None) is None
    ), (
        "extract_plain_text_statute_mentions should be de-deprecated: its "
        "production residue lane was deleted; it is now a non-authoritative "
        "measurement/audit recognizer."
    )


# ---------------------------------------------------------------------------
# Whole-corpus guard (archive-gated, slow): production emits ZERO fallback.
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return (Path(root) / "data" / "finlex.farchive").exists()


def _scan_one_fallback(sid: str) -> int:
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    try:
        xb = store.read_oracle(sid)
    except Exception:
        return 0
    if not xb:
        return 0
    try:
        res = extract_all_reference_mentions(xb, sid)
    except Exception:
        return 0
    return sum(1 for m in res.mentions if m.phrase_lemma == "plain_text_fallback")


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
@pytest.mark.slow
def test_corpus_production_emits_zero_plain_text_fallback() -> None:
    """No statute in the corpus emits the deleted regex-residue fallback lane."""
    from concurrent.futures import ProcessPoolExecutor
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()
    total = 0
    with ProcessPoolExecutor(max_workers=8) as ex:
        for n in ex.map(_scan_one_fallback, ids, chunksize=256):
            total += n
    assert total == 0, (
        f"Production emitted {total} plain_text_fallback mention(s) across the "
        "corpus — the deleted legacy regex residue lane was re-introduced. The "
        "census proved this residue is dead (mis-pivoted duplicates of citations "
        "the construction lane already catches correctly); it must stay deleted."
    )
