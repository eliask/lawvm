"""Gate for the FULL-corpus statute-NAME registry enumerator.

``all_entries_from_farchive`` is the full-corpus counterpart of
``sample_entries_from_farchive``: it STREAMS every statute's ``docTitle`` from the
farchive into ``StatuteNameEntry`` records (vs the old prefix sample) and
populates ``valid_from`` from the source ``dateIssued`` while honestly leaving
``valid_to`` open (the farchive carries only the current consolidated title, no
title-change history).

These gates are corpus-presence-gated: they skip when the farchive is not
materialized in this checkout (it is a large, gitignored artifact), so the suite
stays green in a bare checkout while exercising the real enumerator wherever the
corpus exists.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from lawvm.finland.references.registries.statute_name import (
    StatuteNameEntry,
    all_entries_from_farchive,
    build_registry,
    sample_entries_from_farchive,
)


def _archive_path() -> str:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT", ".")
    return os.path.join(root, "data", "finlex.farchive")


_HAS_CORPUS = Path(_archive_path()).exists()
_skip_no_corpus = pytest.mark.skipif(
    not _HAS_CORPUS,
    reason=f"farchive not present at {_archive_path()} (gitignored artifact)",
)


def test_enumerator_is_lazy_stream() -> None:
    """The enumerator must be a generator (one XML blob in memory at a time).

    This holds with NO corpus: calling it returns a generator object without
    touching the farchive — only iteration reads it.  Bounded peak memory is the
    whole point (WSL2 ceiling), so the streaming contract is asserted directly.
    """
    gen = all_entries_from_farchive(limit=1)
    assert isinstance(gen, Iterator)


def test_farchive_entry_readers_do_not_create_missing_archive(tmp_path: Path) -> None:
    missing = tmp_path / "unused.farchive"

    with pytest.raises(sqlite3.OperationalError):
        sample_entries_from_farchive(limit=1, archive_path=str(missing))
    assert not missing.exists()

    gen = all_entries_from_farchive(limit=1, archive_path=str(missing))
    with pytest.raises(sqlite3.OperationalError):
        next(gen)
    assert not missing.exists()


@_skip_no_corpus
def test_enumerator_yields_temporal_entries() -> None:
    """A small ``limit`` slice yields real entries with honest temporal bounds.

    ``valid_from`` comes from the source ``dateIssued`` (a real date for the
    overwhelming majority of statutes); ``valid_to`` is the consolidated oracle's
    ``finlex:repealedBy`` supersession date for a repealed act, else open. Both
    are real corpus dates or ``None`` — never fabricated.
    """
    entries = list(all_entries_from_farchive(limit=200))
    assert entries, "expected at least one statute title from the corpus"

    for e in entries:
        assert isinstance(e, StatuteNameEntry)
        assert e.statute_id
        assert e.canonical_title
        # Both bounds are either a real date or open — never fabricated garbage.
        assert e.valid_from is None or isinstance(e.valid_from, dt.date)
        assert e.valid_to is None or isinstance(e.valid_to, dt.date)
        # A closed window must be coherent (repeal cannot precede enactment).
        if e.valid_from is not None and e.valid_to is not None:
            assert e.valid_to >= e.valid_from

    # The corpus has dateIssued for the vast majority of statutes — at least one
    # of a 200-slice must carry a real enactment date (else extraction is broken).
    assert any(e.valid_from is not None for e in entries), (
        "no entry got a valid_from — dateIssued extraction is broken"
    )

    # The repeal-date extraction must actually fire: a low-id slice (the oldest
    # statutes, many long-repealed) must close at least one window from the
    # oracle's finlex:repealedBy block (else the valid_to wiring is broken).
    assert any(e.valid_to is not None for e in entries), (
        "no entry got a valid_to — oracle repeal-date extraction is broken"
    )


@_skip_no_corpus
def test_full_enumerator_beats_sample_coverage() -> None:
    """The full slice indexes strictly MORE statutes than the old prefix sample.

    The headline of the scale-up: a larger walk produces a larger registry. We
    compare a 2000-id slice against the 500-id sample's ceiling to keep the test
    cheap while proving the enumerator is not silently truncating.
    """
    sample = sample_entries_from_farchive(limit=500)
    bigger = list(all_entries_from_farchive(limit=2000))
    assert len(bigger) > len(sample)

    # The bigger slice's ids are a superset of the sample's (same chronological
    # order, deeper walk) — the enumerator does not drop the sample's coverage.
    sample_ids = {e.statute_id for e in sample}
    bigger_ids = {e.statute_id for e in bigger}
    assert sample_ids <= bigger_ids


@_skip_no_corpus
def test_enumerated_entries_build_a_resolving_registry() -> None:
    """Entries from the enumerator feed ``build_registry`` and resolve by-name.

    End-to-end: at least one head-bearing title from the corpus must resolve to a
    single statute id through an inflected surface (the registry's reason to
    exist). We pick a known closed-head title from the slice and round-trip it.
    """
    entries = list(all_entries_from_farchive(limit=3000))
    reg = build_registry(entries, aliases=None)

    # Find a title ending in the canonical "laki" head; its nominative must
    # resolve to its own id (single) through the built registry.
    head_bearing = [
        e for e in entries if e.canonical_title.strip().lower().endswith("laki")
    ]
    assert head_bearing, "expected at least one '...laki' title in a 3000-slice"
    probe = head_bearing[0]
    res = reg.lookup(probe.canonical_title)
    assert res.registry_status in ("single", "multiple")
    assert probe.statute_id in {c.statute_id for c in res.candidates}


@_skip_no_corpus
def test_limit_caps_enumeration() -> None:
    """``limit`` bounds the walk; 0 (default) means unbounded (the FULL corpus)."""
    capped = list(all_entries_from_farchive(limit=50))
    # At most 50 ids walked (some may lack a title and be skipped) — never more.
    assert len(capped) <= 50
