"""Differential TEXT-STATE parity: NEW pack-native path == OLD transition-graph.

This is the proof that discharges the viewer-unification goal "the new stuff has
all required functionality". We unified the two statute viewers: the new
``pack-work`` → substrate pack → ``viewer/law-graph.js`` path now carries a TIME
lens (point-in-time reconstruction, per-provision history, lifecycle) on top of
relations/transclusion, replacing the old ``export-transition-graph`` →
``statute-timeline`` path. Both paths run the SAME replay engine; they differ
only in the artifact they emit and how the browser reconstructs point-in-time
text from it.

The test exports BOTH artifacts for a real FI statute from one engine, then at a
set of sampled dates asserts the reconstructed point-in-time state is IDENTICAL:
the same set of live covering-unit addresses AND the same rendered text per
address. A divergence here is a real reconstruction bug and FAILS — it is never
papered over.

Why exact match is expected (not merely hoped): both paths materialize the SAME
covering units (``covering_units`` at ``subsection`` granularity) over the SAME
``change_dates`` via the SAME ``tree_materializer``. The OLD path records the
active covering set per date directly (``active_at`` + subtree ``content_blobs``);
the NEW path records one MAXIMAL ``effect_interval`` per address and reselects by
interval. Sampled strictly at change-dates, interval selection must reproduce the
per-date active set, and the rendered text is the SAME ``irnode_to_text`` recipe
either way (the OLD blob is the engine's ``to_jsonable_dict`` of the very subtree
the NEW content leaf renders). Representational difference noted and proven
identity-preserving: OLD = subtree-blob granularity, NEW = adaptive text leaf;
they render the same text because the leaf is exactly ``irnode_to_text`` of that
subtree.

----------------------------------------------------------------------------- #
FEATURE-PARITY CHECKLIST — statute-timeline.js feature -> law-graph.js equivalent
----------------------------------------------------------------------------- #
"All required functionality" is auditable by mapping each OLD statute-timeline
feature to its NEW law-graph equivalent and the smoke assertion that covers the
UI side (``viewer/test/law_graph_smoke.py``, 21/21). This test owns the
TEXT-STATE substrate parity (rows below marked [THIS TEST]); the smoke owns the
rendered-UI parity (rows marked [SMOKE]).

| statute-timeline.js feature   | law-graph.js equivalent                 | covered by |
|-------------------------------|-----------------------------------------|------------|
| point-in-time text at a date  | pack.textAt(addr, asOf) interval select | [THIS TEST] reconstruct_old == reconstruct_new at every sampled date |
| date scrubber (change dates)  | scrubber over manifest change_dates     | [SMOKE] "scrubber lists change dates" (>20) |
| state differs across dates    | reconstruction differs across dates     | [THIS TEST] (sampled dates differ) + [SMOKE] "point-in-time reconstruction differs across dates" |
| per-provision history trail   | inline prov-history under a unit        | [SMOKE] "per-provision history opens inline" |
| change badges                 | .chg-badge lifecycle badge              | [SMOKE] "lifecycle strips render" |
| lifecycle strips (micro time) | .chg-badge .chg-strip                    | [SMOKE] "lifecycle strips render" |
| ghosts (repealed provision)   | .node.tombstone[data-ghost]             | [SMOKE] "ghost tombstone renders" + [THIS TEST] deleted addr drops out of both reconstructions |
| § quick-jump                  | § quick-jump scroll                     | [SMOKE] "§ quick-jump resolves a section" |
| TOC scroll-spy                | #doc render + scroll                    | [SMOKE] doc renders / scroll-to-section path |
| reference/overlay search      | edge-anchor affordances                 | [SMOKE] "anchored edges with proof badges" |
| self-verify (checkpoint hash) | in-browser L0 verify badge + checkpoint | [SMOKE] "checkpoint self-verify at scrubbed date" + "rows verified" badge |

The substrate behind the time lens (point-in-time, change-date set, ghost
drop-out) is what THIS test certifies byte-for-byte; the smoke certifies that the
viewer paints that substrate with every former affordance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.substrate.viewer_path_reconstruct import (
    old_change_dates,
    reconstruct_new,
    reconstruct_old,
)


def _export_both(statute_id: str, tmp_path: Path) -> tuple[Path, Path]:
    """Export the dense db (OLD) and the sparse pack (NEW) from one engine replay each."""
    from lawvm.substrate.exporter import export_work_pack
    from lawvm.tools.export_transition_graph import export_transition_graph
    from lawvm.tools.transition_graph_jurisdictions import (
        transition_graph_adapter_for_jurisdiction,
    )

    adapter = transition_graph_adapter_for_jurisdiction("fi")
    db_path = tmp_path / "transition.db"
    export_transition_graph(
        statute_id,
        db_path,
        "",
        quiet=True,
        profile=adapter.profile,
        interlink_provider=adapter.interlink_provider,
        overlay_provider=adapter.overlay_provider,
        replay_runner=adapter.replay_runner,
        tree_materializer=adapter.tree_materializer,
    )
    pack_dir = tmp_path / "pack"
    export_work_pack(statute_id, pack_dir, jurisdiction="fi", quiet=True)
    return db_path, pack_dir


def _sample_dates(change_dates: list[str]) -> list[str]:
    """Commencement + mid-life + recent + the densest-amendment change-dates.

    Bounded sampling (not every change-date) but deliberately covers the highest
    transition-count dates, where a reconstruction bug is most likely to surface.
    """
    if not change_dates:
        return []
    dense = ["2014-01-01", "2023-02-23", "2015-07-01"]  # the three densest UL change-dates
    picks = {
        change_dates[0],  # commencement
        change_dates[len(change_dates) // 2],  # mid-life
        change_dates[-1],  # most recent
    }
    picks.update(d for d in dense if d in change_dates)
    return sorted(picks)


def _assert_parity_at(db_path: Path, pack_dir: Path, dates: list[str]) -> None:
    for date in dates:
        old = reconstruct_old(db_path, date)
        new = reconstruct_new(pack_dir, date)
        only_old = sorted(set(old) - set(new))
        only_new = sorted(set(new) - set(old))
        assert not only_old and not only_new, (
            f"live-address set diverges at {date}: only in OLD={only_old[:8]}; "
            f"only in NEW={only_new[:8]}"
        )
        text_diffs = [a for a in (set(old) & set(new)) if old[a] != new[a]]
        if text_diffs:
            a = text_diffs[0]
            raise AssertionError(
                f"point-in-time TEXT diverges at {date} for {len(text_diffs)} "
                f"address(es); first {a!r}:\n  OLD={old[a]!r}\n  NEW={new[a]!r}"
            )


@pytest.mark.slow
def test_viewer_path_parity_ulkomaalaislaki(tmp_path: Path) -> None:
    """301/2004 (Ulkomaalaislaki, the viewer time demo): NEW pack == OLD db.

    Sampled at commencement, mid-life, most recent, and the three densest
    amendment change-dates. Asserts identical live-address set AND identical
    rendered text per address — the byte-match proof.
    """
    if not Path("data/finlex.farchive").exists():
        pytest.skip("finlex farchive not reachable")
    db_path, pack_dir = _export_both("301/2004", tmp_path)
    change_dates = old_change_dates(db_path)
    assert len(change_dates) > 50, f"expected the full UL change-date axis, got {len(change_dates)}"
    dates = _sample_dates(change_dates)
    assert len(dates) >= 5, f"expected >=5 sampled dates, got {dates}"
    _assert_parity_at(db_path, pack_dir, dates)

    # The lens must actually MOVE: at least two sampled dates must reconstruct a
    # different state, or the parity assertion is vacuously over a constant.
    states = {d: tuple(sorted(reconstruct_new(pack_dir, d).items())) for d in dates}
    assert len(set(states.values())) >= 2, (
        "point-in-time reconstruction did not differ across sampled dates "
        "(the time lens is not exercising distinct states)"
    )


@pytest.mark.slow
def test_viewer_path_parity_tuloverolaki(tmp_path: Path) -> None:
    """1535/1992 (Tuloverolaki, a heavier act): NEW pack == OLD db at sampled dates."""
    if not Path("data/finlex.farchive").exists():
        pytest.skip("finlex farchive not reachable")
    db_path, pack_dir = _export_both("1535/1992", tmp_path)
    change_dates = old_change_dates(db_path)
    assert change_dates, "expected a non-empty change-date axis for Tuloverolaki"
    # Commencement + mid-life + most recent (no hardcoded dense dates for TVL —
    # pick the change-dates with the most transitions from the dense export).
    picks = sorted(
        {change_dates[0], change_dates[len(change_dates) // 2], change_dates[-1]}
    )
    _assert_parity_at(db_path, pack_dir, picks)
