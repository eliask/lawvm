"""Rank-21 projection-plane leak gates (untyped_boundary + silent_drop).

Three coupled fixes, one per leak the ledger named:

  (a) ``export_fi_refs._try_write_parquet`` pins the EXPLICIT 20+1-field schema on
      the POPULATED write path, so a column rename/add/drop in the projected rows
      fails LOUD instead of silently writing a from_pylist-inferred schema.

  (b) ``export_parquet`` attaches a per-table ``projection_coverage`` leaf at
      write (parquet metadata + sidecar), so a partial emission cannot read as an
      all-provision-clean artifact.

These exercise the writers directly on synthetic rows — no corpus.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.core.manual_claims.primitive import _ProfileTagDeprecated as ProfileTag


def _minimal_compile_metadata():
    from lawvm.core.compile_metadata import CompileMetadata

    return CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        build_id="rank21-projection-coverage-test",
    )


def _canonical_fi_refs_row() -> dict[str, object]:
    """A populated fi_refs row whose key-set matches the pinned schema exactly.

    Mirrors ``reference_mention_to_row`` (14) + ``_DETERMINISTIC_ROW_EXTRAS`` (6)
    + ``emit_profile`` (1).
    """
    from lawvm.tools.export_fi_refs import _DETERMINISTIC_ROW_EXTRAS

    base: dict[str, object] = {
        "source_statute_id": "999/2099",
        "source_provision_ref_str": "999/2099 1 §",
        "target_statute_id": "527/2014",
        "target_provision_ref_str": "527/2014 5 §",
        "cite_kind": "explicit",
        "cite_confidence": "high",
        "edge_subtype": "internal",
        "phrase_lemma": "sovelletaan",
        "source_span_file": "f.xml",
        "source_span_byte_offset": 10,
        "source_span_len": 7,
        "valid_at_start": "2020-01-01",
        "valid_at_end": None,
        "target_stat_hash": "deadbeef",
    }
    row = dict(base)
    row.update(_DETERMINISTIC_ROW_EXTRAS)
    row["emit_profile"] = ProfileTag.DETERMINISTIC_ONLY.value
    return row


# ---------------------------------------------------------------------------
# (a) fi_refs populated-path schema pin
# ---------------------------------------------------------------------------


def test_fi_refs_populated_path_pins_explicit_schema(tmp_path: Path) -> None:
    """A canonical populated write yields the exact pinned 21-field schema."""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from lawvm.tools.export_fi_refs import _fi_refs_arrow_schema, _try_write_parquet

    out = tmp_path / "fi_refs.parquet"
    ok = _try_write_parquet(
        out,
        [_canonical_fi_refs_row()],
        ProfileTag.DETERMINISTIC_ONLY,
        _minimal_compile_metadata(),
    )
    assert ok
    written = pq.read_table(str(out))
    expected = [f.name for f in _fi_refs_arrow_schema(pa)]
    assert written.schema.names == expected


def test_fi_refs_column_drift_fails_loud(tmp_path: Path) -> None:
    """A renamed/added/dropped column makes the populated write raise (not infer)."""
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_refs import _try_write_parquet

    # Rename a column (drop the canonical name, add a drifted one): the row
    # key-set no longer equals the pinned schema field-set → loud ValueError.
    drifted = _canonical_fi_refs_row()
    drifted["source_statute_id_RENAMED"] = drifted.pop("source_statute_id")

    with pytest.raises(ValueError) as exc:
        _try_write_parquet(
            tmp_path / "drift.parquet",
            [drifted],
            ProfileTag.DETERMINISTIC_ONLY,
            _minimal_compile_metadata(),
        )
    msg = str(exc.value)
    assert "schema drift" in msg
    assert "source_statute_id" in msg  # the missing column is named
    assert "source_statute_id_RENAMED" in msg  # the extra column is named


def test_fi_refs_extra_column_fails_loud(tmp_path: Path) -> None:
    """An EXTRA column (the silent-drop case from_pylist would swallow) raises."""
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_refs import _try_write_parquet

    extra = _canonical_fi_refs_row()
    extra["unexpected_new_column"] = "x"

    with pytest.raises(ValueError) as exc:
        _try_write_parquet(
            tmp_path / "extra.parquet",
            [extra],
            ProfileTag.DETERMINISTIC_ONLY,
            _minimal_compile_metadata(),
        )
    assert "unexpected_new_column" in str(exc.value)


def test_fi_refs_type_drift_fails_loud(tmp_path: Path) -> None:
    """A column whose value type no longer matches the pinned type raises."""
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_refs import _try_write_parquet

    bad = _canonical_fi_refs_row()
    bad["source_span_byte_offset"] = "not-an-int"  # schema pins int64

    with pytest.raises(ValueError) as exc:
        _try_write_parquet(
            tmp_path / "type.parquet",
            [bad],
            ProfileTag.DETERMINISTIC_ONLY,
            _minimal_compile_metadata(),
        )
    assert "TYPE drift" in str(exc.value)


# ---------------------------------------------------------------------------
# (b) export_parquet projection_coverage leaf
# ---------------------------------------------------------------------------


def test_projection_coverage_statutes_omitted_count() -> None:
    """The statutes table owes one row per corpus statute; a shortfall is counted."""
    from lawvm.tools.export_parquet import _projection_coverage

    rows = [
        {"statute_id": "1/2020"},
        {"statute_id": "2/2020"},
    ]
    leaf = _projection_coverage(table_name="statutes", rows=rows, corpus_size=5)
    assert leaf["universe_kind"] == "corpus_statute_set"
    assert leaf["corpus_size"] == 5
    assert leaf["row_count"] == 2
    assert leaf["statutes_with_rows"] == 2
    # 5 corpus statutes, only 2 statute rows → 3 silently-dropped become VISIBLE.
    assert leaf["omitted_row_count"] == 3


def test_projection_coverage_fanout_table_no_false_omission() -> None:
    """Per-statute fan-out tables (sections/ops) don't claim omission for 0-row statutes."""
    from lawvm.tools.export_parquet import _projection_coverage

    rows = [{"statute_id": "1/2020"}, {"statute_id": "1/2020"}]
    leaf = _projection_coverage(table_name="sections", rows=rows, corpus_size=5)
    assert leaf["row_count"] == 2
    assert leaf["statutes_with_rows"] == 1
    assert leaf["omitted_row_count"] == 0  # zero sections for a statute is not a defect


def test_projection_coverage_leaf_attached_to_parquet_metadata(tmp_path: Path) -> None:
    """The coverage leaf is recoverable from the written parquet's schema metadata."""
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from lawvm.tools.export_parquet import _projection_coverage, _try_write_parquet

    rows = [{"statute_id": "1/2020", "score": 0.9}]
    coverage = _projection_coverage(table_name="statutes", rows=rows, corpus_size=3)
    out = tmp_path / "statutes.parquet"
    ok = _try_write_parquet(
        out, rows, _minimal_compile_metadata(), coverage=coverage
    )
    assert ok
    meta = pq.read_table(str(out)).schema.metadata or {}
    assert b"lawvm.projection_coverage" in meta
    recovered = json.loads(meta[b"lawvm.projection_coverage"].decode())
    assert recovered["corpus_size"] == 3
    assert recovered["row_count"] == 1
    assert recovered["omitted_row_count"] == 2
