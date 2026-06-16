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
