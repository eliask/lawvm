"""Unit tests for the chunked/resumable all_pit driver (#187).

Covers, with NO corpus builds (the per-statute scorer is monkeypatched to a
deterministic stub):

* chunk-partition logic (deterministic, order-preserving, size clamping),
* journal round-trip (write → read a ChunkRecord; result dict ↔ dataclass),
* resume-from-partial (a run that stops after k chunks resumes and only
  computes the missing chunks; the aggregate is unchanged),
* aggregation equals the single-shot path over the same fixture corpus.
"""
from __future__ import annotations

import json

import pytest

from lawvm.tools import all_pit_driver
from lawvm.tools.all_pit_driver import (
    ALL_PIT_JOURNAL_SCHEMA,
    ChunkRecord,
    _statute_result_from_dict,
    _statute_result_to_dict,
    partition_chunks,
    read_chunk_record,
    run_all_pit_chunked,
    write_chunk_record,
)
from lawvm.tools.bench import (
    _AllPitSnapshotResult,
    _AllPitStatuteResult,
    _run_all_pit,
)


# ---------------------------------------------------------------------------
# Deterministic stub scorer — one snapshot per statute, sim derived from sid.
# ---------------------------------------------------------------------------


def _stub_score(sid: str) -> _AllPitStatuteResult:
    """Deterministic fake result: a two-snapshot statute whose sims depend on sid."""
    year = int(sid.split("/")[0])
    base = 1.0 - ((year % 5) / 100.0)  # in [0.96, 1.0]
    return _AllPitStatuteResult(
        sid=sid,
        snapshots=(
            _AllPitSnapshotResult(
                version_tag=f"{sid}-v1",
                amendment_id=f"{sid}-a1",
                as_of="2000-01-01",
                struct_sim=base,
                n_sections=10,
                n_penalized=int(round((1 - base) * 10)),
                phase_status="OK",
            ),
            _AllPitSnapshotResult(
                version_tag=f"{sid}-v2",
                amendment_id=f"{sid}-a2",
                as_of="2010-01-01",
                struct_sim=1.0,
                n_sections=10,
                n_penalized=0,
                phase_status="OK",
            ),
        ),
        phase_status="OK",
    )


@pytest.fixture
def fixture_sids() -> list[str]:
    return [f"{2000 + i}/{i + 1}" for i in range(23)]


@pytest.fixture(autouse=True)
def _patch_scorer(monkeypatch):
    """Route both the driver's chunk executor and the single-shot path through
    the deterministic stub (serial worker path)."""
    monkeypatch.setattr(
        "lawvm.tools.bench._all_pit_score_one_statute", _stub_score, raising=True
    )


# ---------------------------------------------------------------------------
# chunk partition
# ---------------------------------------------------------------------------


def test_partition_chunks_is_deterministic_and_order_preserving():
    sids = [f"s{i}" for i in range(10)]
    chunks = partition_chunks(sids, 3)
    assert chunks == [
        ["s0", "s1", "s2"],
        ["s3", "s4", "s5"],
        ["s6", "s7", "s8"],
        ["s9"],
    ]
    # flattening recovers the original order exactly.
    assert [s for c in chunks for s in c] == sids


def test_partition_chunks_clamps_nonpositive_size():
    sids = ["a", "b", "c"]
    assert partition_chunks(sids, 0) == [["a"], ["b"], ["c"]]
    assert partition_chunks(sids, -5) == [["a"], ["b"], ["c"]]


def test_partition_chunks_single_chunk_when_size_exceeds_len():
    assert partition_chunks(["a", "b"], 100) == [["a", "b"]]


# ---------------------------------------------------------------------------
# result serialization round-trip
# ---------------------------------------------------------------------------


def test_statute_result_dict_round_trip():
    res = _stub_score("2003/7")
    doc = _statute_result_to_dict(res)
    back = _statute_result_from_dict(doc)
    assert back.sid == res.sid
    assert back.phase_status == res.phase_status
    assert len(back.snapshots) == len(res.snapshots)
    for a, b in zip(back.snapshots, res.snapshots, strict=True):
        assert a == b


# ---------------------------------------------------------------------------
# journal write/read round-trip
# ---------------------------------------------------------------------------


def test_chunk_record_round_trip(tmp_path):
    results = [_stub_score("2001/1"), _stub_score("2002/2")]
    record = ChunkRecord(
        chunk_index=3,
        sids=("2001/1", "2002/2"),
        results=tuple(_statute_result_to_dict(r) for r in results),
    )
    write_chunk_record(tmp_path, record)
    # persisted file carries the versioned schema tag.
    path = tmp_path / "chunk_00003.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == ALL_PIT_JOURNAL_SCHEMA
    back = read_chunk_record(tmp_path, 3)
    assert back is not None
    assert back.chunk_index == 3
    assert back.sids == ("2001/1", "2002/2")
    assert back.results == record.results


def test_read_missing_or_malformed_chunk_returns_none(tmp_path):
    assert read_chunk_record(tmp_path, 0) is None
    bad = tmp_path / "chunk_00000.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert read_chunk_record(tmp_path, 0) is None
    # wrong schema is also treated as absent.
    (tmp_path / "chunk_00001.json").write_text(
        json.dumps({"schema": "other", "chunk_index": 1, "sids": [], "results": []}),
        encoding="utf-8",
    )
    assert read_chunk_record(tmp_path, 1) is None


# ---------------------------------------------------------------------------
# aggregation equals single-shot
# ---------------------------------------------------------------------------


def _summarize(results) -> tuple:
    """A stable, comparable digest of an _AllPitStatuteResult list."""
    return tuple(
        (
            r.sid,
            r.phase_status,
            tuple(
                (s.version_tag, round(s.struct_sim, 9), s.n_sections, s.n_penalized)
                for s in r.snapshots
            ),
        )
        for r in results
    )


def test_chunked_aggregate_equals_single_shot(tmp_path, fixture_sids):
    single = _run_all_pit(fixture_sids, workers=1, verbose=False)
    chunked = run_all_pit_chunked(
        fixture_sids,
        workers=1,
        chunk_size=5,
        run_dir=tmp_path / "run",
        resume=True,
        verbose=False,
    )
    assert [r.sid for r in chunked] == fixture_sids
    assert _summarize(chunked) == _summarize(single)


# ---------------------------------------------------------------------------
# resume-from-partial
# ---------------------------------------------------------------------------


def test_resume_from_partial_reproduces_full_aggregate(tmp_path, fixture_sids, monkeypatch):
    run_dir = tmp_path / "run"
    chunk_size = 5

    # Simulate an interrupted run: pre-populate the journal with only the first
    # two chunks (as the real driver would have written them mid-run).
    from lawvm.tools.all_pit_driver import partition_chunks as _pc

    chunks = _pc(fixture_sids, chunk_size)
    for idx in (0, 1):
        results = [_stub_score(s) for s in chunks[idx]]
        write_chunk_record(
            run_dir,
            ChunkRecord(
                chunk_index=idx,
                sids=tuple(chunks[idx]),
                results=tuple(_statute_result_to_dict(r) for r in results),
            ),
        )
    # A manifest matching this corpus/chunk_size must exist for resume to engage.
    all_pit_driver._write_run_manifest(
        run_dir,
        sids=fixture_sids,
        chunk_size=chunk_size,
        workers=1,
        n_chunks=len(chunks),
    )

    # Track which sids actually get (re)computed after resume.
    computed: list[str] = []

    def _tracking_score(sid: str) -> _AllPitStatuteResult:
        computed.append(sid)
        return _stub_score(sid)

    monkeypatch.setattr(
        "lawvm.tools.bench._all_pit_score_one_statute", _tracking_score, raising=True
    )
    resumed = run_all_pit_chunked(
        fixture_sids,
        workers=1,
        chunk_size=chunk_size,
        run_dir=run_dir,
        resume=True,
        verbose=False,
    )

    # The first two chunks (10 sids) were journaled → NOT recomputed.
    assert set(computed).isdisjoint(set(fixture_sids[:10]))
    # Everything from chunk index 2 onward WAS computed.
    assert set(computed) == set(fixture_sids[10:])

    # And the resumed aggregate equals a clean full run.
    clean = run_all_pit_chunked(
        fixture_sids,
        workers=1,
        chunk_size=chunk_size,
        run_dir=tmp_path / "clean",
        resume=False,
        verbose=False,
    )
    assert _summarize(resumed) == _summarize(clean)


def test_stale_corpus_journal_is_refused_and_run_starts_fresh(tmp_path, fixture_sids):
    run_dir = tmp_path / "run"
    # Write a journal + manifest for a DIFFERENT corpus.
    other = ["1900/1", "1901/2"]
    all_pit_driver._write_run_manifest(
        run_dir, sids=other, chunk_size=5, workers=1, n_chunks=1
    )
    write_chunk_record(
        run_dir,
        ChunkRecord(
            chunk_index=0,
            sids=tuple(other),
            results=tuple(_statute_result_to_dict(_stub_score(s)) for s in other),
        ),
    )
    # Resuming with the real fixture corpus must ignore the stale journal.
    results = run_all_pit_chunked(
        fixture_sids,
        workers=1,
        chunk_size=5,
        run_dir=run_dir,
        resume=True,
        verbose=False,
    )
    assert [r.sid for r in results] == fixture_sids
