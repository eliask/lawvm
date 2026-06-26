"""CI guard: the three rank-3 normalize fallback heuristics are load-bearing.

The three regex op-heuristics in :mod:`lawvm.finland.normalize`
(``parse_ops_fallback_heuristic``, its production shadow
``parse_ops_fallback_heuristic_with_coverage``, and ``parse_ops_title_fallback``)
were DE-DEPRECATED after a whole-corpus census proved each is a load-bearing
required residual the typed grammar cannot own (they fire only on the
``if not ops:`` branch — exactly when the deterministic parse declined). This is
the committed, regenerable proof that pins that claim.

The load-bearing witness is production-faithful: a FINAL compiled op carrying the
heuristic's extraction-provenance tag
(:data:`~lawvm.finland.normalize_fallback_heuristic_census.JOHTO_FALLBACK_TAG` /
``TITLE_FALLBACK_TAG``) is, by construction, an op the heuristic produced and the
typed grammar declined. The pinned baselines below are the EXACT per-statute
load-bearing op counts measured by the whole-corpus census
(:mod:`lawvm.finland.normalize_fallback_heuristic_census`) at base ``f5843e95``.

Two layers:

* Registry/shape tests (always run, corpus-free): the tag set is closed and the
  pinned baselines are well-formed (positive counts, totals reconcile).

* Per-firing load-bearing guard (archive-gated): replays EXACTLY the pinned
  load-bearing statutes via the production replay and asserts each still emits
  the pinned number of tagged compiled ops. A statute that stops emitting its
  tagged ops (the grammar absorbed the shape — the heuristic is now deletable
  there) OR emits a different count fails loudly, forcing a deliberate
  re-measure + baseline bump. Replaying only the pinned ~76 statutes (not the
  whole 59k corpus) keeps the guard fast while proving each firing per-statute.

Regenerate after a legitimate change (a migrated shape leaves the set, lowering
the totals) by re-running the census and updating the two dicts + totals:
    uv run python -m lawvm.finland.normalize_fallback_heuristic_census --json
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.finland.normalize_fallback_heuristic_census import (
    HEURISTIC_TAGS,
    JOHTO_FALLBACK_TAG,
    TITLE_FALLBACK_TAG,
    _scan_one,
)

# ---------------------------------------------------------------------------
# Pinned load-bearing baselines (statute_id -> # of FINAL compiled ops carrying
# the heuristic's tag). Measured live on the full canonical corpus at base
# f5843e95. A human re-measures + bumps these when a shape is legitimately
# migrated into the grammar (which REMOVES the statute from the set).
# ---------------------------------------------------------------------------
JOHTO_LOAD_BEARING: dict[str, int] = {
    "1734/4-000": 9,
    "1868/31-000": 2,
    "1889/39-001": 4,
    "1895/37-001": 8,
    "1919/94-001": 1,
    "1942/747": 1,
    "1943/662": 16,
    "1947/328": 1,
    "1956/347": 1,
    "1956/594": 3,
    "1956/72": 1,
    "1958/299": 1,
    "1958/370": 3,
    "1958/482": 2,
    "1959/266": 6,
    "1961/264": 6,
    "1961/395": 2,
    "1961/404": 3,
    "1962/134": 3,
    "1965/490": 1,
    "1966/280": 7,
    "1968/360": 2,
    "1969/521": 1,
    "1973/272": 1,
    "1974/16": 1,
    "1977/122": 1,
    "1977/73": 2,
    "1978/38": 1,
    "1979/1062": 1,
    "1979/129": 1,
    "1980/669": 2,
    "1987/1203": 1,
    "1988/694": 4,
    "1988/695": 4,
    "1989/495": 3,
    "1989/819": 1,
    "1990/650": 2,
    "1991/206": 1,
    "1991/800": 1,
    "1992/705": 5,
    "1993/1187": 1,
    "1993/130": 1,
    "1993/1501": 4,
    "1993/1607": 1,
    "1993/47": 1,
    "1994/1143": 1,
    "1994/1396": 3,
    "1994/383": 2,
    "1994/763": 1,
    "1995/241": 1,
    "1997/1412": 1,
    "1999/48": 1,
    "2000/808": 1,
    "2002/1192": 1,
    "2002/1290": 2,
    "2005/768": 1,
    "2006/44": 1,
    "2008/878": 1,
    "2009/1169": 2,
    "2013/870": 1,
    "2014/122": 4,
    "2017/320": 1,
    "2017/884": 1,
}
#: title-only fallback: each load-bearing statute recovers exactly one
#: chapter/part/section repeal op visible only in the amendment title.
TITLE_LOAD_BEARING: dict[str, int] = {
    "1961/368": 1,
    "1969/152": 1,
    "1982/1116": 1,
    "1990/845": 1,
    "1991/1725": 1,
    "1993/1607": 1,
    "1994/750": 1,
    "1994/856": 1,
    "1996/1118": 1,
    "1998/745": 1,
    "2004/1338": 1,
    "2006/1401": 1,
    "2023/380": 1,
}

JOHTO_STATUTES_BASELINE = len(JOHTO_LOAD_BEARING)
JOHTO_OPS_BASELINE = sum(JOHTO_LOAD_BEARING.values())
TITLE_STATUTES_BASELINE = len(TITLE_LOAD_BEARING)
TITLE_OPS_BASELINE = sum(TITLE_LOAD_BEARING.values())


# ---------------------------------------------------------------------------
# Registry / shape (corpus-free, always run)
# ---------------------------------------------------------------------------
def test_tag_set_is_closed() -> None:
    assert HEURISTIC_TAGS == (JOHTO_FALLBACK_TAG, TITLE_FALLBACK_TAG)
    assert len(set(HEURISTIC_TAGS)) == 2


def test_pinned_baselines_well_formed() -> None:
    assert all(c >= 1 for c in JOHTO_LOAD_BEARING.values())
    assert all(c >= 1 for c in TITLE_LOAD_BEARING.values())
    assert JOHTO_OPS_BASELINE == sum(JOHTO_LOAD_BEARING.values())
    assert TITLE_OPS_BASELINE == sum(TITLE_LOAD_BEARING.values())
    # Both heuristics fire load-bearingly somewhere (none is dead).
    assert JOHTO_STATUTES_BASELINE > 0
    assert TITLE_STATUTES_BASELINE > 0


# ---------------------------------------------------------------------------
# Per-firing load-bearing guard (archive-gated)
# ---------------------------------------------------------------------------
def _canonical_corpus_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if root and (Path(root) / "data" / "finlex.farchive").exists():
        return True
    # Also accept a repo-local populated corpus (worktree symlink / main repo).
    here = Path(__file__).resolve().parents[1]
    return (here / "data" / "finlex.farchive").exists()


def _replay_tag_counts(sid: str) -> dict[str, int]:
    res = _scan_one(sid)
    if res is None:
        return {tag: 0 for tag in HEURISTIC_TAGS}
    return res[1]


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked",
)
@pytest.mark.slow
def test_johto_fallback_per_statute_load_bearing() -> None:
    """Every pinned johtolause-fallback statute still emits its tagged ops."""
    drifted: list[str] = []
    for sid, expected in sorted(JOHTO_LOAD_BEARING.items()):
        got = _replay_tag_counts(sid).get(JOHTO_FALLBACK_TAG, 0)
        if got != expected:
            drifted.append(f"  {sid}: expected {expected} tagged ops, got {got}")
    assert not drifted, (
        "parse_ops_fallback_heuristic load-bearing output drifted; the grammar "
        "may have absorbed (got<expected => deletable there) or a shape changed:\n"
        + "\n".join(drifted)
        + "\n\nRe-measure with the census and update JOHTO_LOAD_BEARING:\n"
        "  uv run python -m lawvm.finland.normalize_fallback_heuristic_census --json"
    )


@pytest.mark.skipif(
    not _canonical_corpus_available(),
    reason="canonical finlex.farchive not linked",
)
@pytest.mark.slow
def test_title_fallback_per_statute_load_bearing() -> None:
    """Every pinned title-fallback statute still emits its tagged ops."""
    drifted: list[str] = []
    for sid, expected in sorted(TITLE_LOAD_BEARING.items()):
        got = _replay_tag_counts(sid).get(TITLE_FALLBACK_TAG, 0)
        if got != expected:
            drifted.append(f"  {sid}: expected {expected} tagged ops, got {got}")
    assert not drifted, (
        "parse_ops_title_fallback load-bearing output drifted:\n"
        + "\n".join(drifted)
        + "\n\nRe-measure with the census and update TITLE_LOAD_BEARING:\n"
        "  uv run python -m lawvm.finland.normalize_fallback_heuristic_census --json"
    )
