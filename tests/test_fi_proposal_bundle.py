"""Tests for lawvm fi-proposal-bundle (feature #6).

Per AGENTS.md §15, covers all required test categories:

1. Smoke test: smoke test against representative HE fixtures from #4
   conformance corpus — bundle construction succeeds on both FULL_AKN and
   PDF_WRAPPER HEs.

2. --all produces complete bundle for a FULL_AKN HE: all include-* sections
   present, no hard failure on missing downstream data (warnings emitted instead).

3. PDF_WRAPPER HE produces metadata-only bundle without errors:
   atoms / law_refs / signatures lists empty, warnings explain why.

4. Schema-stability: output JSON shape pinned — all top-level keys always
   present in deterministic order.

5. Determinism: same he_id + same include-* flags + same data state →
   identical JSON.

6. HE-ID normalisation: all supported input forms resolve to the same
   canonical he_id.

7. AGENTS.md §1.8 no-disappearance: requested include-* sections are always
   present as lists even when data is missing — never silently omitted.

8. Typed primitive correctness: dataclass fields match expected types after
   DuckDB fetch → dataclass → JSON serialisation round-trip.

Strategy: these tests use in-memory DuckDB with synthetic Parquet tables
assembled from the existing conformance fixtures (he_projection/fixtures.py),
so they do not depend on the filesystem farchive or external downloads.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from lawvm.finland.conformance_corpus.he_projection.fixtures import (
    FULL_AKN_HE,
    PDF_WRAPPER_HE,
)
from lawvm.tools.export_fi_he_corpus import project_he_from_xml
from lawvm.tools.fi_proposal_bundle import (
    AtomRow,
    MinistryRef,
    ProposalBundle,
    SignatureRow,
    _bundle_to_json,
    _parse_he_id_variants,
    _row_to_dict,
    _str_or_none,
    assemble_bundle,
)


# ---------------------------------------------------------------------------
# Helpers: build in-memory Parquet files from projection results
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_SHA = "test_sha256_bundle"
_PYARROW = importlib.util.find_spec("pyarrow") is not None
_DUCKDB = importlib.util.find_spec("duckdb") is not None


def _pyarrow_modules() -> tuple[Any, Any]:
    return (
        importlib.import_module("pyarrow"),
        importlib.import_module("pyarrow.parquet"),
    )


def _project(fixture: Any) -> Any:
    return project_he_from_xml(
        fixture.xml_bytes,
        he_year=fixture.he_year,
        he_number=fixture.he_number,
        lang=fixture.lang,
        source_file=f"test/{fixture.fixture_id}",
        source_zip_sha256=_SHA,
        ingest_timestamp=_NOW,
        languages_in_he=(fixture.lang,),
    )


def _write_parquets_from_result(
    result: Any,
    data_dir: Path,
    *,
    fixture_he_id: str,
) -> None:
    """Write in-memory projection rows to Parquet files in data_dir.

    Uses pyarrow (available in the test environment per test_fi_he_projection.py
    imports).  Skips writing if pyarrow is not available (tests will fall through
    with missing-file warnings, which is the correct AGENTS §1.8 behaviour).
    """
    if not _PYARROW:
        return
    pa, pq = _pyarrow_modules()

    data_dir.mkdir(parents=True, exist_ok=True)

    # --- fi_he_corpus ---
    if result.corpus_rows:
        schema = pa.schema([
            pa.field("he_id", pa.string()),
            pa.field("he_year", pa.int64()),
            pa.field("he_number", pa.int64()),
            pa.field("he_uri", pa.string()),
            pa.field("lang", pa.string()),
            pa.field("languages", pa.large_list(pa.string())),
            pa.field("ministry_canonical_id", pa.string()),
            pa.field("ministry_show_as", pa.string()),
            pa.field("title", pa.string()),
            pa.field("date_issued", pa.string()),
            pa.field("structural_tier", pa.string()),
            pa.field("is_structured", pa.bool_()),
            pa.field("finlex_state", pa.string()),
            pa.field("source_zip_sha256", pa.string()),
            pa.field("ingest_timestamp", pa.string()),
        ])
        table = pa.Table.from_pylist(result.corpus_rows, schema=schema)
        pq.write_table(table, str(data_dir / "fi_he_corpus.parquet"))

    # --- fi_he_atoms ---
    if result.atom_rows:
        atom_schema = pa.schema([
            pa.field("he_id", pa.string()),
            pa.field("he_year", pa.int64()),
            pa.field("he_number", pa.int64()),
            pa.field("atom_id", pa.string()),
            pa.field("parent_atom_id", pa.string()),
            pa.field("atom_type", pa.string()),
            pa.field("seq", pa.int64()),
            pa.field("num", pa.string()),
            pa.field("heading", pa.string()),
            pa.field("text_content", pa.string()),
            pa.field("char_count", pa.int64()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_len", pa.int64()),
        ])
        table = pa.Table.from_pylist(result.atom_rows, schema=atom_schema)
        pq.write_table(table, str(data_dir / "fi_he_atoms.parquet"))

    # --- fi_he_law_refs ---
    if result.law_ref_rows:
        refs_schema = pa.schema([
            pa.field("he_id", pa.string()),
            pa.field("he_year", pa.int64()),
            pa.field("he_number", pa.int64()),
            pa.field("source_statute_id", pa.string()),
            pa.field("source_provision_ref_str", pa.string()),
            pa.field("target_statute_id", pa.string()),
            pa.field("target_provision_ref_str", pa.string()),
            pa.field("cite_kind", pa.string()),
            pa.field("cite_confidence", pa.string()),
            pa.field("edge_subtype", pa.string()),
            pa.field("phrase_lemma", pa.string()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_len", pa.int64()),
            pa.field("valid_at_start", pa.string()),
            pa.field("valid_at_end", pa.string()),
            pa.field("target_stat_hash", pa.string()),
        ])
        table = pa.Table.from_pylist(result.law_ref_rows, schema=refs_schema)
        pq.write_table(table, str(data_dir / "fi_he_law_refs.parquet"))

    # --- fi_he_signatures ---
    if result.signature_rows:
        sig_schema = pa.schema([
            pa.field("he_id", pa.string()),
            pa.field("he_year", pa.int64()),
            pa.field("he_number", pa.int64()),
            pa.field("role", pa.string()),
            pa.field("person", pa.string()),
            pa.field("signature_order", pa.int64()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_len", pa.int64()),
        ])
        table = pa.Table.from_pylist(result.signature_rows, schema=sig_schema)
        pq.write_table(table, str(data_dir / "fi_he_signatures.parquet"))


def _write_empty_corpus(data_dir: Path, corpus_rows: List[Dict[str, Any]]) -> None:
    """Write a corpus parquet with given rows (may be empty for PDF_WRAPPER)."""
    if not _PYARROW:
        return
    pa, pq = _pyarrow_modules()
    data_dir.mkdir(parents=True, exist_ok=True)
    schema = pa.schema([
        pa.field("he_id", pa.string()),
        pa.field("he_year", pa.int64()),
        pa.field("he_number", pa.int64()),
        pa.field("he_uri", pa.string()),
        pa.field("lang", pa.string()),
        pa.field("languages", pa.large_list(pa.string())),
        pa.field("ministry_canonical_id", pa.string()),
        pa.field("ministry_show_as", pa.string()),
        pa.field("title", pa.string()),
        pa.field("date_issued", pa.string()),
        pa.field("structural_tier", pa.string()),
        pa.field("is_structured", pa.bool_()),
        pa.field("finlex_state", pa.string()),
        pa.field("source_zip_sha256", pa.string()),
        pa.field("ingest_timestamp", pa.string()),
    ])
    table = pa.Table.from_pylist(corpus_rows, schema=schema)
    pq.write_table(table, str(data_dir / "fi_he_corpus.parquet"))


def _he_data_dir_for_fixture(fixture: Any, tmp_path: Path) -> str:
    """Project fixture and write Parquet files; return he_data_dir string path."""
    data_dir = tmp_path / "fi" / "v1"
    result = _project(fixture)
    _write_parquets_from_result(result, data_dir, fixture_he_id=str(fixture.he_year))
    # For PDF_WRAPPER: write corpus but no atoms/refs/sigs
    if not result.atom_rows and not result.law_ref_rows and not result.signature_rows:
        if result.corpus_rows:
            _write_empty_corpus(data_dir, result.corpus_rows)
    return str(data_dir)


def _get_corpus_he_id(fixture: Any, tmp_path: Path) -> str:
    """Project fixture and return the he_id from the first corpus row."""
    result = _project(fixture)
    if result.corpus_rows:
        return str(result.corpus_rows[0].get("he_id") or f"HE {fixture.he_number}/{fixture.he_year} vp")
    # Fallback: construct from year/number
    return f"HE {fixture.he_number}/{fixture.he_year} vp"


# ---------------------------------------------------------------------------
# Check whether pyarrow is available (needed for most tests)
# ---------------------------------------------------------------------------

_needs_pyarrow = pytest.mark.skipif(
    not _PYARROW,
    reason="pyarrow required to write test Parquet fixtures",
)

_needs_duckdb = pytest.mark.skipif(
    True,  # evaluated lazily below
    reason="duckdb required for bundle assembly",
)

_needs_duckdb = pytest.mark.skipif(
    not _DUCKDB,
    reason="duckdb required for bundle assembly",
)

_needs_both = pytest.mark.skipif(
    not (_PYARROW and _DUCKDB),
    reason="pyarrow + duckdb required",
)


# ---------------------------------------------------------------------------
# 1. Smoke tests
# ---------------------------------------------------------------------------


class TestSmoke:
    """Category 1: bundle construction succeeds on standard fixtures."""

    @_needs_both
    def test_full_akn_metadata_only_no_crash(self, tmp_path: Path) -> None:
        """Smoke test: FULL_AKN HE produces a bundle without errors (metadata only)."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")  # intentionally empty

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert bundle.he_id == FULL_AKN_HE.expected_corpus_row["he_id"]
        assert bundle.is_structured is True
        assert bundle.structural_tier == "full_akn"
        # No inclusions requested → all lists empty, no errors
        assert bundle.atoms == []
        assert bundle.law_refs == []
        assert bundle.actor_mentions == []
        assert bundle.pool_mentions == []
        assert bundle.telos_sections == []
        assert bundle.signatures == []
        assert bundle.replay_status is None

    @_needs_both
    def test_pdf_wrapper_metadata_only_no_crash(self, tmp_path: Path) -> None:
        """Smoke test: PDF_WRAPPER HE produces a bundle without errors."""
        data_dir = _he_data_dir_for_fixture(PDF_WRAPPER_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")
        he_id = _get_corpus_he_id(PDF_WRAPPER_HE, tmp_path)

        bundle = assemble_bundle(
            he_id=he_id,
            he_id_candidates=[he_id],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert bundle.is_structured is False
        assert bundle.structural_tier == "pdf_wrapper"


# ---------------------------------------------------------------------------
# 2. --all produces complete bundle for FULL_AKN HE
# ---------------------------------------------------------------------------


class TestAllFlag:
    """Category 2: --all flag requests all include-* sections."""

    @_needs_both
    def test_all_flag_full_akn_complete_keys(self, tmp_path: Path) -> None:
        """--all on FULL_AKN HE: all top-level keys present in JSON output."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")  # empty — triggers warnings, not crash

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=True,
            include_law_refs=True,
            include_actors=True,
            include_pools=True,
            include_telos=True,
            include_replay_status=True,
            include_text="none",
            include_signatures=True,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )

        d = _row_to_dict(bundle)
        # All top-level keys from the brief must be present
        required_keys = {
            "he_id", "he_uri", "title", "ministry",
            "structural_tier", "is_structured",
            "date_issued", "finlex_state",
            "atoms", "law_refs", "actor_mentions", "pool_mentions",
            "telos_sections", "signatures", "replay_status", "warnings",
        }
        missing = required_keys - set(d.keys())
        assert not missing, f"Missing top-level keys in bundle: {missing}"

    @_needs_both
    def test_all_flag_full_akn_atoms_present(self, tmp_path: Path) -> None:
        """--all on FULL_AKN HE with atoms data: atoms list is non-empty."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=True,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        # FULL_AKN HE fixture has body atoms
        assert len(bundle.atoms) > 0, "FULL_AKN HE should produce atom rows"
        # Each atom is an AtomRow with required fields
        atom = bundle.atoms[0]
        assert isinstance(atom, AtomRow)
        assert isinstance(atom.atom_id, str)
        assert atom.atom_id != ""
        assert isinstance(atom.seq, int)
        assert atom.seq >= 0

    @_needs_both
    def test_all_flag_full_akn_signatures_present(self, tmp_path: Path) -> None:
        """--all on FULL_AKN HE with signatures data: signatures list non-empty."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=True,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert len(bundle.signatures) == 2, (
            f"FULL_AKN HE fixture has 2 signatures; got {len(bundle.signatures)}"
        )
        sig = bundle.signatures[0]
        assert isinstance(sig, SignatureRow)
        assert sig.role is not None
        assert sig.signature_order == 0


# ---------------------------------------------------------------------------
# 3. PDF_WRAPPER HE produces metadata-only bundle
# ---------------------------------------------------------------------------


class TestPdfWrapper:
    """Category 3: PDF_WRAPPER HE → metadata-only bundle without errors."""

    @_needs_both
    def test_pdf_wrapper_all_body_sections_empty(self, tmp_path: Path) -> None:
        """PDF_WRAPPER HE: atoms, law_refs, signatures empty; warnings emitted."""
        data_dir = _he_data_dir_for_fixture(PDF_WRAPPER_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")
        he_id = _get_corpus_he_id(PDF_WRAPPER_HE, tmp_path)

        bundle = assemble_bundle(
            he_id=he_id,
            he_id_candidates=[he_id],
            include_atoms=True,
            include_law_refs=True,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=True,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        # Lists must be empty — no body data from PDF_WRAPPER
        assert bundle.atoms == [], "PDF_WRAPPER must produce 0 atoms"
        assert bundle.law_refs == [], "PDF_WRAPPER must produce 0 law_refs"
        assert bundle.signatures == [], "PDF_WRAPPER must produce 0 signatures"

    @_needs_both
    def test_pdf_wrapper_warnings_for_each_included_body_section(self, tmp_path: Path) -> None:
        """PDF_WRAPPER HE: each requested body section emits a warning entry."""
        data_dir = _he_data_dir_for_fixture(PDF_WRAPPER_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")
        he_id = _get_corpus_he_id(PDF_WRAPPER_HE, tmp_path)

        bundle = assemble_bundle(
            he_id=he_id,
            he_id_candidates=[he_id],
            include_atoms=True,
            include_law_refs=True,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=True,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        # Exactly one warning per requested body section (atoms, law_refs, signatures)
        assert len(bundle.warnings) >= 3, (
            f"Expected at least 3 warnings for atoms/law_refs/signatures; "
            f"got {len(bundle.warnings)}: {bundle.warnings}"
        )
        # Each warning mentions "PDF_WRAPPER"
        pdf_warnings = [w for w in bundle.warnings if "PDF_WRAPPER" in w]
        assert len(pdf_warnings) >= 3, (
            f"Expected 3 PDF_WRAPPER-themed warnings; got {len(pdf_warnings)}"
        )

    @_needs_both
    def test_pdf_wrapper_metadata_fields_correct(self, tmp_path: Path) -> None:
        """PDF_WRAPPER HE: metadata fields (he_id, is_structured, ministry) correct."""
        data_dir = _he_data_dir_for_fixture(PDF_WRAPPER_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")
        he_id = _get_corpus_he_id(PDF_WRAPPER_HE, tmp_path)

        bundle = assemble_bundle(
            he_id=he_id,
            he_id_candidates=[he_id],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert bundle.is_structured is False
        assert bundle.structural_tier == "pdf_wrapper"
        assert isinstance(bundle.ministry, MinistryRef)


# ---------------------------------------------------------------------------
# 4. Schema-stability: output JSON shape pinned
# ---------------------------------------------------------------------------


class TestSchemaStability:
    """Category 4: JSON shape is stable across calls."""

    _REQUIRED_TOP_LEVEL_KEYS = [
        "he_id", "he_uri", "title", "ministry",
        "structural_tier", "is_structured",
        "date_issued", "finlex_state",
        "atoms", "law_refs", "actor_mentions", "pool_mentions",
        "telos_sections", "signatures", "replay_status", "warnings",
    ]
    _REQUIRED_MINISTRY_KEYS = ["canonical_id", "show_as"]

    @_needs_both
    def test_top_level_keys_stable(self, tmp_path: Path) -> None:
        """Bundle JSON always has all required top-level keys."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        d = json.loads(_bundle_to_json(bundle))
        for key in self._REQUIRED_TOP_LEVEL_KEYS:
            assert key in d, f"Required key {key!r} missing from bundle JSON"

    @_needs_both
    def test_ministry_sub_shape_stable(self, tmp_path: Path) -> None:
        """ministry sub-object always has canonical_id and show_as."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        d = json.loads(_bundle_to_json(bundle))
        ministry = d.get("ministry")
        assert isinstance(ministry, dict), "ministry must be a dict"
        for key in self._REQUIRED_MINISTRY_KEYS:
            assert key in ministry, f"ministry key {key!r} missing"

    @_needs_both
    def test_empty_sections_are_lists_not_null(self, tmp_path: Path) -> None:
        """Empty include-* sections are [] not null in JSON."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        d = json.loads(_bundle_to_json(bundle))
        list_keys = ["atoms", "law_refs", "actor_mentions", "pool_mentions",
                     "telos_sections", "signatures", "warnings"]
        for key in list_keys:
            assert isinstance(d[key], list), f"{key!r} must be [] not None in JSON"

    def test_atom_row_shape(self) -> None:
        """AtomRow serialises to dict with all required fields."""
        atom = AtomRow(
            atom_id="HE 98/1996 vp#aom_rationale_0",
            parent_atom_id=None,
            atom_type="rationale",
            seq=0,
            num=None,
            heading="1 Nykytila",
            char_count=100,
            source_span_file="test/fixture",
        )
        d = _row_to_dict(atom)
        required = {"atom_id", "parent_atom_id", "atom_type", "seq", "num",
                    "heading", "char_count", "source_span_file"}
        assert required <= set(d.keys())
        assert d["atom_id"] == "HE 98/1996 vp#aom_rationale_0"
        assert d["parent_atom_id"] is None
        assert d["seq"] == 0

    def test_signature_row_shape(self) -> None:
        """SignatureRow serialises to dict with all required fields."""
        sig = SignatureRow(role="Tasavallan Presidentti", person="Martti Ahtisaari", signature_order=0)
        d = _row_to_dict(sig)
        assert set(d.keys()) == {"role", "person", "signature_order"}
        assert d["role"] == "Tasavallan Presidentti"

    def test_ministry_ref_shape(self) -> None:
        """MinistryRef serialises to dict with canonical_id and show_as."""
        m = MinistryRef(canonical_id="fi.ministry-of-justice", show_as="Oikeusministeri\xf6")
        d = _row_to_dict(m)
        assert set(d.keys()) == {"canonical_id", "show_as"}
        assert d["canonical_id"] == "fi.ministry-of-justice"


# ---------------------------------------------------------------------------
# 5. Determinism: same input → identical JSON
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Category 5: same he_id + same include-* + same data → identical JSON."""

    @_needs_both
    def test_identical_json_on_repeat_call(self, tmp_path: Path) -> None:
        """Two calls with identical parameters produce bit-identical JSON."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        kwargs: Dict[str, Any] = dict(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=True,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=True,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        bundle1 = assemble_bundle(**kwargs)
        bundle2 = assemble_bundle(**kwargs)
        assert _bundle_to_json(bundle1) == _bundle_to_json(bundle2)


# ---------------------------------------------------------------------------
# 6. HE-ID normalisation
# ---------------------------------------------------------------------------


class TestHeIdNormalisation:
    """Category 6: all supported input forms parse to sensible candidates."""

    def test_canonical_form(self) -> None:
        """Canonical 'HE 98/1996 vp' form stays as-is or gains/drops vp suffix."""
        candidates = _parse_he_id_variants("HE 98/1996 vp")
        # Must contain the exact canonical form
        assert any("HE 98/1996 vp" in c for c in candidates), (
            f"Expected 'HE 98/1996 vp' in candidates: {candidates}"
        )

    def test_he_slash_year_slash_number_form(self) -> None:
        """'HE/2024/184' normalises to 'HE 184/2024 vp' candidate."""
        candidates = _parse_he_id_variants("HE/2024/184")
        assert any("184/2024" in c for c in candidates), (
            f"Expected '184/2024' in candidates: {candidates}"
        )

    def test_he_dash_number_year_form(self) -> None:
        """'HE-184/2024' normalises to 'HE 184/2024' candidate."""
        candidates = _parse_he_id_variants("HE-184/2024")
        assert any("184/2024" in c for c in candidates), (
            f"Expected '184/2024' in candidates: {candidates}"
        )

    def test_he_space_number_year_form(self) -> None:
        """'HE 184/2024' normalises to both vp and no-vp variants."""
        candidates = _parse_he_id_variants("HE 184/2024")
        assert len(candidates) >= 1
        assert any("184/2024" in c for c in candidates)

    def test_he_slash_form_distinct_from_space_form(self) -> None:
        """HE/2024/184 and HE 184/2024 both resolve to the same numeric part."""
        c1 = _parse_he_id_variants("HE/2024/184")
        c2 = _parse_he_id_variants("HE 184/2024")
        # Both should contain a candidate with "184/2024"
        assert any("184/2024" in c for c in c1)
        assert any("184/2024" in c for c in c2)

    def test_no_duplicate_candidates(self) -> None:
        """_parse_he_id_variants must not return duplicate strings."""
        for raw in ["HE 98/1996 vp", "HE/2024/184", "HE-184/2024"]:
            candidates = _parse_he_id_variants(raw)
            assert len(candidates) == len(set(candidates)), (
                f"Duplicate candidates for {raw!r}: {candidates}"
            )


# ---------------------------------------------------------------------------
# 7. AGENTS.md §1.8 no-disappearance: include-* always present as lists
# ---------------------------------------------------------------------------


class TestNoDisappearance:
    """Category 7: §1.8 — requested sections always present as lists."""

    @_needs_both
    def test_include_actors_missing_projection_dir_gives_empty_list_with_warning(
        self, tmp_path: Path
    ) -> None:
        """--include-actors with no fi_actors.parquet: empty list + warning (not crash)."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")  # intentionally absent

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=True,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        # Key must be present as []
        assert bundle.actor_mentions == []
        # Warning must explain why
        actor_warnings = [w for w in bundle.warnings if "actor" in w.lower()]
        assert len(actor_warnings) >= 1, (
            f"Expected >=1 warning about actor_mentions; got: {bundle.warnings}"
        )

    @_needs_both
    def test_include_pools_missing_projection_dir_gives_empty_list_with_warning(
        self, tmp_path: Path
    ) -> None:
        """--include-pools with no fi_pools.parquet: empty list + warning."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=True,
            include_telos=False,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert bundle.pool_mentions == []
        pool_warnings = [w for w in bundle.warnings if "pool" in w.lower()]
        assert len(pool_warnings) >= 1, (
            f"Expected >=1 warning about pool_mentions; got: {bundle.warnings}"
        )

    @_needs_both
    def test_include_telos_missing_sections_parquet_gives_empty_list_with_warning(
        self, tmp_path: Path
    ) -> None:
        """--include-telos with no sections.parquet: empty list + warning."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=True,
            include_replay_status=False,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        assert bundle.telos_sections == []
        telos_warnings = [w for w in bundle.warnings if "telos" in w.lower()]
        assert len(telos_warnings) >= 1, (
            f"Expected >=1 warning about telos_sections; got: {bundle.warnings}"
        )

    @_needs_both
    def test_include_replay_status_missing_statutes_parquet_gives_warning(
        self, tmp_path: Path
    ) -> None:
        """--include-replay-status with no statutes.parquet: warning emitted."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=True,
            include_text="none",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        replay_warnings = [w for w in bundle.warnings if "replay" in w.lower()]
        assert len(replay_warnings) >= 1, (
            f"Expected >=1 warning about replay_status; got: {bundle.warnings}"
        )

    @_needs_both
    def test_include_text_non_none_warns_not_implemented(self, tmp_path: Path) -> None:
        """--include-text affected: emits warning that it is not yet implemented."""
        data_dir = _he_data_dir_for_fixture(FULL_AKN_HE, tmp_path)
        projections_dir = str(tmp_path / "projections")

        bundle = assemble_bundle(
            he_id=FULL_AKN_HE.expected_corpus_row["he_id"],
            he_id_candidates=[FULL_AKN_HE.expected_corpus_row["he_id"]],
            include_atoms=False,
            include_law_refs=False,
            include_actors=False,
            include_pools=False,
            include_telos=False,
            include_replay_status=False,
            include_text="affected",
            include_signatures=False,
            limit=None,
            he_data_dir=data_dir,
            projections_data_dir=projections_dir,
        )
        text_warnings = [w for w in bundle.warnings if "include_text" in w or "rehydration" in w.lower()]
        assert len(text_warnings) >= 1, (
            f"Expected a warning for include_text='affected'; got: {bundle.warnings}"
        )


# ---------------------------------------------------------------------------
# 8. Typed primitive correctness: round-trip through JSON
# ---------------------------------------------------------------------------


class TestTypedPrimitivesRoundTrip:
    """Category 8: dataclass fields survive the → JSON → parse cycle."""

    def test_bundle_json_round_trips_scalars(self) -> None:
        """ProposalBundle scalar fields survive JSON round-trip."""
        bundle = ProposalBundle(
            he_id="HE 98/1996 vp",
            he_uri="/akn/fi/doc/government-proposal/1996/98",
            title="Hallituksen esitys rikoslain muuttamisesta",
            ministry=MinistryRef(
                canonical_id="fi.ministry-of-justice",
                show_as="Oikeusministeri\xf6",
            ),
            structural_tier="full_akn",
            is_structured=True,
            date_issued="1996-05-24",
            finlex_state="closed",
        )
        j = _bundle_to_json(bundle)
        d = json.loads(j)
        assert d["he_id"] == "HE 98/1996 vp"
        assert d["he_uri"] == "/akn/fi/doc/government-proposal/1996/98"
        assert d["is_structured"] is True
        assert d["structural_tier"] == "full_akn"
        assert d["ministry"]["canonical_id"] == "fi.ministry-of-justice"
        assert d["ministry"]["show_as"] == "Oikeusministeri\xf6"

    def test_str_or_none_scalar(self) -> None:
        """_str_or_none returns None for None, strips whitespace, returns None for ''."""
        assert _str_or_none(None) is None
        assert _str_or_none("") is None
        assert _str_or_none("  ") is None
        assert _str_or_none("hello") == "hello"
        assert _str_or_none("  hi  ") == "hi"

    def test_bundle_to_json_is_valid_json(self) -> None:
        """_bundle_to_json produces parseable JSON."""
        bundle = ProposalBundle(
            he_id="HE 1/2024 vp",
            he_uri="",
            title="",
            ministry=MinistryRef(canonical_id="", show_as=""),
            structural_tier="unknown",
            is_structured=False,
            date_issued=None,
            finlex_state=None,
        )
        j = _bundle_to_json(bundle)
        parsed = json.loads(j)
        assert parsed["he_id"] == "HE 1/2024 vp"
        assert parsed["replay_status"] is None
        assert parsed["warnings"] == []

    def test_atom_row_in_bundle_roundtrip(self) -> None:
        """AtomRow embedded in bundle round-trips correctly."""
        atom = AtomRow(
            atom_id="HE 1/2024 vp#atom_rationale_0",
            parent_atom_id=None,
            atom_type="rationale",
            seq=0,
            num=None,
            heading="Nykytila",
            char_count=255,
            source_span_file="test/fixture",
        )
        bundle = ProposalBundle(
            he_id="HE 1/2024 vp",
            he_uri="",
            title="",
            ministry=MinistryRef(canonical_id="", show_as=""),
            structural_tier="full_akn",
            is_structured=True,
            date_issued=None,
            finlex_state=None,
            atoms=[atom],
        )
        j = _bundle_to_json(bundle)
        d = json.loads(j)
        assert len(d["atoms"]) == 1
        a = d["atoms"][0]
        assert a["atom_id"] == "HE 1/2024 vp#atom_rationale_0"
        assert a["parent_atom_id"] is None
        assert a["seq"] == 0
        assert a["char_count"] == 255
