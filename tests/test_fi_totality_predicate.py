"""Regression pins for the raw-tape no-silent-drop totality predicate (#37).

The predicate is warn-only telemetry: it flags uncovered, non-benign operative
labels as candidate silent drops, and (under LAWVM_PARSE_TOTALITY) surfaces them
as `silent_drop` residuals on the parse result. It never raises and never changes
a parse.

Expected flag/no-flag values below were derived by running the predicate over the
Finlex corpus and cross-checked against the prototype's full-corpus run
(89% agreement with the parse_bench classifier; the mine-only set = annotation-
hidden drops). The pinned sids:

  * 2009/886 -- the historic 69j/k/l, 71 §, 138 § drops are now covered; the
    predicate must report ZERO flags over the whole (69-op) clause.
  * 1967/484, 1970/16, 1983/223, 1987/618 -- annotation-hidden true-positive
    drops the prototype found; each must stay FLAGGED (>= 1 uncovered_operative).
  * 1978/588 -- a clean benign case (witness fidelity): 18 ops, ZERO flags.
  * 2009/749 -- a title-suffix DECLINE (n_ops == 0): the corpus harness excludes
    n_ops==0 clauses from candidate DROPs, so this is NOT a silent drop.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause.totality import FlaggedDrop, predicate


def _finlex_corpus_available() -> bool:
    # Resolve through the canonical precedence (worktrees may have no local
    # data/ but point at the shared checkout via LAWVM_CANONICAL_DATA_ROOT).
    try:
        from lawvm.corpus_store import resolve_farchive_path

        path, _rule = resolve_farchive_path("finlex.farchive")
        return path.exists()
    except Exception:
        return False


_FINLEX_CORPUS_AVAILABLE = _finlex_corpus_available()

pytestmark = pytest.mark.skipif(
    not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available"
)


def _johtolause(sid: str) -> str:
    from lawvm.corpus_store import get_corpus_store
    from lawvm.finland.metadata import get_johtolause

    store = get_corpus_store(readonly=True)
    xml_bytes = store.read_source(sid) or store.read_amendment(sid)
    assert xml_bytes, f"no source for {sid}"
    return get_johtolause(xml_bytes) or ""


def _run(sid: str) -> tuple[list[FlaggedDrop], int]:
    return predicate(_johtolause(sid))


# --- (a) 2009/886: historic drops now covered -> zero flags, full op set --------


def test_2009_886_no_flags_full_op_set() -> None:
    flagged, n_ops = _run("2009/886")
    assert flagged == [], [
        (f.label.label, f.label.struct_cat, f.reason) for f in flagged
    ]
    # The historic 69 j/k/l, 71 §, 138 § targets are part of this clause; it
    # produces a large op set and the predicate sees full coverage.
    assert n_ops == 69


# --- (b) annotation-hidden true-positive drops -> flagged ------------------------


@pytest.mark.parametrize(
    ("sid", "expect_label", "expect_struct"),
    [
        ("1967/484", "4", "PYKALA"),
        ("1970/16", "5", "PYKALA"),
        ("1983/223", "115", "PYKALA"),
        ("1987/618", "1", "PYKALA"),
    ],
)
def test_annotation_hidden_drops_flagged(
    sid: str, expect_label: str, expect_struct: str
) -> None:
    flagged, n_ops = _run(sid)
    # A genuine silent drop: an op was produced but a sibling target was lost.
    assert n_ops > 0
    assert flagged, f"{sid} expected >= 1 flag, got none"
    assert all(f.reason == "uncovered_operative" for f in flagged)
    pairs = {(f.label.label, f.label.struct_cat) for f in flagged}
    assert (expect_label, expect_struct) in pairs, pairs
    # Self-evidencing: the flag carries the exact unparsed source text.
    assert all(f.source_text for f in flagged)


# --- (c) benign cases -> not a candidate silent drop ----------------------------


def test_1978_588_benign_witness_fidelity_not_flagged() -> None:
    # Witness-fidelity benign: 18 ops, every operative label covered or shielded.
    flagged, n_ops = _run("1978/588")
    assert n_ops == 18
    assert flagged == [], [
        (f.label.label, f.label.struct_cat) for f in flagged
    ]


def test_2009_749_title_suffix_decline_not_a_silent_drop() -> None:
    # Title-suffix DECLINE: the clause produces no op (n_ops == 0), so it is a
    # different, already-loud failure mode -- the corpus harness excludes such
    # clauses from the candidate-DROP precision metric.
    _flagged, n_ops = _run("2009/749")
    assert n_ops == 0


# --- (d) container-context guard: LUKU/OSA container of a covered section --------
#
# A LUKU/OSA that is the structural CONTAINER of a covered `N §` target (chapter
# context `N luvun M §`, appendix part `II osan [P luvun] M §`, `N lukuun uusi
# M §`) is benign container context, NOT a dropped operative target -- but only
# when the covered `§` is reached through a tight locative chain (a coordinated
# sibling `§` past a comma/conjunction does not shield). These sids were
# adjudicated FALSE POSITIVES of the prototype run; the guard must now leave them
# with ZERO flags.


@pytest.mark.parametrize(
    "sid",
    [
        "2018/539",   # II osan 3 luvun 1 §:ään (1 § covered) -> osa = part-container
        "2018/579",   # I osan 1 luvun 2 §:ään (2 § covered) + uusi II A osa
        "2018/1303",  # II osan 5/6 luvun 6/8 §:ään (both covered)
        "2019/173",   # II osan 10 luvun 1/2 §:ään (covered)
        "2020/1068",  # 3 lukuun uusi 7 § (7 § covered)
        "2022/491",   # 18 lukuun uusi 19 a § (19a covered)
        "2023/205",   # 5 lukuun uusi 13 a § (13a covered)
    ],
)
def test_container_context_luku_osa_not_flagged(sid: str) -> None:
    flagged, n_ops = _run(sid)
    assert n_ops > 0
    assert flagged == [], [
        (f.label.label, f.label.struct_cat) for f in flagged
    ]


def test_container_guard_keeps_coordinated_sibling_chapter_flagged() -> None:
    # 1996/473: `7 luvun otsikko sekä 50―55 §` -- the `7 luku` (heading target) is
    # separated from any covered `§` by `otsikko` and `sekä`, so it is NOT a
    # §-container and MUST stay flagged (a genuine dropped chapter-heading target),
    # even though a *different* `7 lukuun uusi 52 a §` instance later in the clause
    # IS a covered container. The dropped target survives.
    flagged, n_ops = _run("1996/473")
    assert n_ops > 0
    pairs = {(f.label.struct_cat, f.label.label) for f in flagged}
    assert ("LUKU", "7") in pairs, pairs


# --- (e) appendix-table-part guard: fee-table/luettelo part-selector ------------
#
# The appendix edit ``asetuksen liitteenä olevan maksutaulukon|luettelon <ROMAN>
# osan ... kohta`` produces ONE number-less ``kind == "A"`` op. The roman-numeral
# OSA part-selector glued to the appendix-content word is a coordinate into that
# appendix, not a standalone dropped ``osa`` -> it must NOT flag. A *coordinated*
# sibling part (``ja III osan ...``) is a second appendix part the single A-op does
# not cover, so it stays flagged (genuine under-segmentation). These sids were
# adjudicated FALSE POSITIVES of the prototype run.


@pytest.mark.parametrize(
    "sid",
    [
        "1993/735",  # luettelon IV osa
        "2003/159",  # maksutaulukon VI osan 2 kohta
        "2004/908",  # maksutaulukon IV osaan uusi kohta
        "2006/763",  # maksutaulukon VIII osaan uusi kohta
        "2007/862",  # maksutaulukon VII osaan uusi kohta
        "2008/624",  # maksutaulukon VI osaan uusi kohta
    ],
)
def test_appendix_table_part_selector_not_flagged(sid: str) -> None:
    flagged, n_ops = _run(sid)
    assert n_ops > 0
    # The single appendix part-selector is the only operative label these clauses
    # leave uncovered; with the guard it must clear to zero.
    assert flagged == [], [(f.label.label, f.label.struct_cat) for f in flagged]


# --- (f) corrigendum-footnote FP class: cleared at the extraction layer ----------
#
# `get_johtolause` strips `<authorialNote>` corrigendum footnotes (which wrap the
# SUPERSEDED original wording, "alkuperaeinen sanamuoto kului: ...") before
# extracting the clause text. Before that strip, the superseded section labels
# inside those notes leaked into the johtolause and the predicate flagged them as
# uncovered operative drops -- a benign FALSE-POSITIVE class (the labels are
# historical noise, not live targets). Isolating the strip over the candidate-drop
# corpus showed it clears 78 flags across 17 sids (16 fully to zero).
#
# These sids each carry an authorialNote corrigendum footnote and previously
# flagged (1..26 spurious drops); with the extraction strip in place the predicate
# must now see ZERO flags over the (live) op set. This pins the cleared FP class so
# a regression in the strip (re-leaking footnote labels) is caught here.


@pytest.mark.parametrize(
    ("sid", "expect_n_ops"),
    [
        ("1991/176", 134),  # was 26 footnote-leaked flags -> 0
        ("2000/886", 27),   # was 16 -> 0
        ("1997/638", 25),   # was 7 -> 0
        ("1992/1519", 26),  # 1992/15xx cluster, was 2 -> 0
    ],
)
def test_corrigendum_footnote_labels_not_flagged(sid: str, expect_n_ops: int) -> None:
    flagged, n_ops = _run(sid)
    assert n_ops == expect_n_ops
    assert flagged == [], [
        (f.label.label, f.label.struct_cat, f.source_text[:60]) for f in flagged
    ]


# --- appendix sub-part tail recovery ---------------------------------------------
#
# The appendix sub-edit tail (surface_parse._appendix_subpart_tail /
# containers._appendix_subpart_tail) covers the ``<num/roman/letter> osa[n]``
# part-selectors and coordinated ``<num> liitteen`` siblings the base appendix ref
# would otherwise drop. These sids each previously carried >= 1 uncovered OSA/LIITE
# appendix sub-part drop; the tail recognizer must now cover every appendix
# part-selector so NO OSA/LIITE label survives as an uncovered drop. Pins the
# recovered slice so a regression in the tail recognizer re-leaks these.


@pytest.mark.parametrize(
    "sid",
    [
        "2010/883",   # maksutaulukon I osan ... ja III osan ... (part-selector chain)
        "1993/736",   # luettelon I osan otsikko ja II osa (coordinated osa sibling)
        "2002/1145",  # 1 liitteen 4.2.3 kohta ja 2 liitteen 6 kohta (liite sibling)
        "2000/1207",  # liitteen 2 osa A ja B (post-noun letter placement)
        "2014/219",   # liitteen 1 I osan johdanto-osa (num + roman part-selector)
        "2006/263",   # liitteen 2 osa B (post-noun letter)
    ],
)
def test_appendix_subpart_tail_no_osa_liite_drop(sid: str) -> None:
    flagged, _n_ops = _run(sid)
    leftover = [
        (f.label.struct_cat, f.label.label)
        for f in flagged
        if f.label.struct_cat in ("OSA", "LIITE")
    ]
    assert leftover == [], leftover


def test_appendix_subpart_tail_recovers_coordinated_sibling_part() -> None:
    # 2010/883: `maksutaulukon I osan ... kohta ja III osan ... kohta`. The appendix
    # sub-edit tail (surface_parse._appendix_subpart_tail) now COVERS both the
    # primary `I osa` AND the coordinated sibling `III osa` part-selectors, so
    # neither survives as an uncovered drop. (Previously the single number-less
    # `kind == "A"` op covered only the primary selector and the sibling stayed
    # flagged as genuine under-segmentation; the tail recognizer recovered it.)
    flagged, n_ops = _run("2010/883")
    assert n_ops > 0
    pairs = {(f.label.struct_cat, f.label.label) for f in flagged}
    assert ("OSA", "iii") not in pairs, pairs
    assert ("OSA", "i") not in pairs, pairs


# --- warn-only wiring: LAWVM_PARSE_TOTALITY surfaces residuals, never raises -----


def test_parse_clause_totality_flag_emits_silent_drop_residual(monkeypatch) -> None:
    from lawvm.finland.johtolause.api import parse_clause

    text = _johtolause("1967/484")

    monkeypatch.setenv("LAWVM_PARSE_TOTALITY", "1")
    result = parse_clause(text)  # must not raise

    silent = [r for r in result.residuals if r.get("kind") == "silent_drop"]
    assert silent, "flag-on parse should surface at least one silent_drop residual"
    one = silent[0]
    assert one["tier"] == "uncovered_operative"
    assert one["unmatched_labels"]  # self-evidencing label|unit
    assert one["source_text"]  # exact unparsed text
    assert isinstance(one["position"], tuple)


def test_parse_clause_totality_flag_off_no_silent_drop(monkeypatch) -> None:
    from lawvm.finland.johtolause.api import parse_clause

    text = _johtolause("1967/484")

    monkeypatch.delenv("LAWVM_PARSE_TOTALITY", raising=False)
    result = parse_clause(text)  # default path: predicate not run

    silent = [r for r in result.residuals if r.get("kind") == "silent_drop"]
    assert silent == []
