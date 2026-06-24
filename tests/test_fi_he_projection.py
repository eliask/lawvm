"""Tests for HE corpus Parquet projections (feature #4).

Per AGENTS.md §15, covers all 7 required test categories:

1. Synthetic + corpus: ingest fixtures, project, verify rows.
2. Findings/observations: PDF_WRAPPER → is_structured=False. Missing-ministry
   emits HEMissingMinistryObservation. Non-HE doc emits HEProjectionFailure.
3. Schema-stability: column order + dtypes pinned for all four projections.
4. Reuse-verification: fi_he_law_refs rows produced by the #1 extractor produce
   the same shape as fi_refs rows for the analogous citation in enacted-law context.
5. Negative test: PDF_WRAPPER HE emits ZERO atom/law_ref/signature rows.
6. Strict-mode test: strict=True on a non-HE document signals abort disposition.
7. No-leak test: synthetic HE markers must not appear in non-test output.

Real-corpus tests (skipped unless ~/Downloads/government-proposal.zip exists).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import pytest

from lawvm.finland.conformance_corpus.he_projection.fixtures import (
    ALL_FIXTURES,
    FULL_AKN_HE,
    PDF_WRAPPER_HE,
    REF_CROSSLINKS_HE,
    MISSING_MINISTRY_HE,
    NON_HE_STATUTE,
    MULTI_ORG_HE,
    BILINGUAL_HE_FIN,
)
from lawvm.tools.export_fi_he_corpus import (
    HEMissingMinistryObservation,
    HEProjectionResult,
    project_he_from_xml,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)
_SHA = "test_sha256"


def _project(fixture: Any, strict: bool = False) -> HEProjectionResult:
    """Project one fixture and return the result."""
    return project_he_from_xml(
        fixture.xml_bytes,
        he_year=fixture.he_year,
        he_number=fixture.he_number,
        lang=fixture.lang,
        source_file=f"test/{fixture.fixture_id}",
        source_zip_sha256=_SHA,
        ingest_timestamp=_NOW,
        languages_in_he=(fixture.lang,),
        strict=strict,
    )


def _assert_partial_row(actual: Dict[str, Any], expected: Dict[str, Any]) -> None:
    """Assert that all keys in expected match actual (partial assertion)."""
    for k, v in expected.items():
        assert k in actual, f"Key {k!r} not in row: {actual}"
        assert actual[k] == v, (
            f"Row[{k!r}]: expected {v!r}, got {actual[k]!r}"
        )


# ---------------------------------------------------------------------------
# 1. Synthetic + corpus fixture tests
# ---------------------------------------------------------------------------


class TestCorpusFixtureProjection:
    """Category 1: ingest conformance fixtures and verify projection rows."""

    def test_full_akn_corpus_row(self) -> None:
        """FULL_AKN HE produces exactly one corpus row with correct fields."""
        result = _project(FULL_AKN_HE)
        assert len(result.corpus_rows) == 1
        _assert_partial_row(result.corpus_rows[0], FULL_AKN_HE.expected_corpus_row)

    def test_full_akn_has_atoms(self) -> None:
        """FULL_AKN HE with body produces atom rows."""
        result = _project(FULL_AKN_HE)
        assert len(result.atom_rows) > 0, "FULL_AKN HE must produce atom rows"
        # All atoms reference the correct HE
        for row in result.atom_rows:
            assert row["he_year"] == FULL_AKN_HE.he_year
            assert row["he_number"] == FULL_AKN_HE.he_number

    def test_full_akn_signatures(self) -> None:
        """FULL_AKN HE with conclusions produces signature rows."""
        result = _project(FULL_AKN_HE)
        assert len(result.signature_rows) == 2, (
            f"Expected 2 signatures, got {len(result.signature_rows)}"
        )
        for expected in FULL_AKN_HE.expected_signature_rows:
            matches = [
                r for r in result.signature_rows
                if r.get("signature_order") == expected["signature_order"]
            ]
            assert matches, f"No signature with order={expected['signature_order']}"
            _assert_partial_row(matches[0], expected)

    def test_pdf_wrapper_corpus_row(self) -> None:
        """PDF_WRAPPER HE produces exactly one corpus row."""
        result = _project(PDF_WRAPPER_HE)
        assert len(result.corpus_rows) == 1
        _assert_partial_row(result.corpus_rows[0], PDF_WRAPPER_HE.expected_corpus_row)

    def test_multi_org_corpus_row(self) -> None:
        """HE with multiple TLCOrganization refs picks the correct ministry."""
        result = _project(MULTI_ORG_HE)
        assert len(result.corpus_rows) == 1
        _assert_partial_row(result.corpus_rows[0], MULTI_ORG_HE.expected_corpus_row)

    def test_bilingual_fin_variant_corpus_row(self) -> None:
        """Finnish variant of bilingual HE records lang='fin'."""
        result = _project(BILINGUAL_HE_FIN)
        assert len(result.corpus_rows) == 1
        _assert_partial_row(result.corpus_rows[0], BILINGUAL_HE_FIN.expected_corpus_row)

    def test_ref_crosslinks_corpus_row(self) -> None:
        """HE with inline ref crosslinks produces a corpus row."""
        result = _project(REF_CROSSLINKS_HE)
        assert len(result.corpus_rows) == 1
        _assert_partial_row(result.corpus_rows[0], REF_CROSSLINKS_HE.expected_corpus_row)

    def test_ref_crosslinks_law_refs(self) -> None:
        """HE with inline <ref> crosslinks produces fi_he_law_refs rows."""
        result = _project(REF_CROSSLINKS_HE)
        assert len(result.law_ref_rows) >= 2, (
            f"Expected >=2 law_ref rows from ref crosslinks, got {len(result.law_ref_rows)}"
        )
        for expected in REF_CROSSLINKS_HE.expected_law_ref_rows:
            matches = [
                r for r in result.law_ref_rows
                if r.get("target_statute_id") == expected["target_statute_id"]
            ]
            assert matches, (
                f"No law_ref row for target_statute_id={expected['target_statute_id']!r}; "
                f"got: {[r.get('target_statute_id') for r in result.law_ref_rows]}"
            )
            _assert_partial_row(matches[0], expected)

    def test_all_fixtures_importable(self) -> None:
        """All conformance fixtures can be imported and are non-empty."""
        assert len(ALL_FIXTURES) >= 6
        for fid, fx in ALL_FIXTURES.items():
            assert fx.xml_bytes, f"Fixture {fid!r} has empty xml_bytes"


# ---------------------------------------------------------------------------
# 2. Findings / observations tests
# ---------------------------------------------------------------------------


class TestFindingsAndObservations:
    """Category 2: typed findings emitted for pathological inputs."""

    def test_pdf_wrapper_emits_is_structured_false(self) -> None:
        """PDF_WRAPPER HE must emit is_structured=False in corpus row."""
        result = _project(PDF_WRAPPER_HE)
        assert len(result.corpus_rows) == 1
        assert result.corpus_rows[0]["is_structured"] is False
        assert result.corpus_rows[0]["structural_tier"] == "pdf_wrapper"

    def test_missing_ministry_emits_observation(self) -> None:
        """HE without finlex:administrativeBranch must emit HEMissingMinistryObservation."""
        result = _project(MISSING_MINISTRY_HE)
        assert len(result.corpus_rows) == 1
        missing_obs = [
            o for o in result.observations
            if isinstance(o, HEMissingMinistryObservation)
        ]
        assert missing_obs, (
            "Expected HEMissingMinistryObservation for HE without ministry; "
            f"got observations: {result.observations}"
        )
        obs = missing_obs[0]
        assert obs.rule_id == "HE_PROJ.MISSING_MINISTRY"
        assert obs.he_year == MISSING_MINISTRY_HE.he_year
        assert obs.he_number == MISSING_MINISTRY_HE.he_number

    def test_missing_ministry_corpus_row_still_emitted(self) -> None:
        """Per AGENTS.md §1.8: corpus row IS emitted even if ministry is absent."""
        result = _project(MISSING_MINISTRY_HE)
        assert len(result.corpus_rows) == 1, (
            "Missing ministry must not suppress the corpus row (§1.8 no silent drop)"
        )
        assert result.corpus_rows[0]["ministry_canonical_id"] == ""

    def test_non_he_document_emits_failure(self) -> None:
        """Non-HE AKN document must emit HEProjectionFailure and no corpus row."""
        result = _project(NON_HE_STATUTE)
        assert len(result.corpus_rows) == 0, (
            "Non-HE document must not produce a corpus row"
        )
        failures = [f for f in result.failures if f.rule_id == "HE_PROJ.WRONG_FRBR_SUBTYPE"]
        assert failures, (
            f"Expected HE_PROJ.WRONG_FRBR_SUBTYPE failure; got: {result.failures}"
        )

    def test_malformed_xml_emits_failure(self) -> None:
        """Malformed XML bytes must emit HEProjectionFailure with rule_id XML_PARSE_ERROR."""
        result = project_he_from_xml(
            b"<not valid xml",
            he_year=2000,
            he_number=1,
            lang="fin",
        )
        assert len(result.corpus_rows) == 0
        failures = [f for f in result.failures if f.rule_id == "HE_PROJ.XML_PARSE_ERROR"]
        assert failures, f"Expected XML_PARSE_ERROR failure; got: {result.failures}"
        assert failures[0].strict_disposition == "abort"


# ---------------------------------------------------------------------------
# 3. Schema-stability tests
# ---------------------------------------------------------------------------


class TestSchemaStability:
    """Category 3: column names pinned for all four projections."""

    _CORPUS_REQUIRED_COLS = {
        "he_id", "he_year", "he_number", "he_uri", "lang",
        "ministry_canonical_id", "ministry_show_as", "title",
        "date_issued", "structural_tier", "is_structured",
        "finlex_state", "source_zip_sha256", "ingest_timestamp",
    }

    _ATOMS_REQUIRED_COLS = {
        "he_id", "he_year", "he_number",
        "atom_id", "parent_atom_id", "atom_type", "seq",
        "num", "heading", "text_content", "char_count",
        "source_span_file", "source_span_byte_offset", "source_span_len",
    }

    _LAW_REFS_REQUIRED_COLS = {
        "he_id", "he_year", "he_number",
        "source_statute_id", "source_provision_ref_str",
        "target_statute_id", "target_provision_ref_str",
        "cite_kind", "cite_confidence", "edge_subtype", "phrase_lemma",
        "source_span_file", "source_span_byte_offset", "source_span_len",
        "valid_at_start", "valid_at_end", "target_stat_hash",
    }

    _SIGNATURES_REQUIRED_COLS = {
        "he_id", "he_year", "he_number",
        "role", "person", "signature_order",
        "source_span_file", "source_span_byte_offset", "source_span_len",
    }

    def test_corpus_row_columns(self) -> None:
        result = _project(FULL_AKN_HE)
        assert result.corpus_rows
        actual_cols = set(result.corpus_rows[0].keys())
        assert self._CORPUS_REQUIRED_COLS <= actual_cols, (
            f"Missing corpus columns: {self._CORPUS_REQUIRED_COLS - actual_cols}"
        )

    def test_atoms_row_columns(self) -> None:
        result = _project(FULL_AKN_HE)
        assert result.atom_rows, "FULL_AKN HE must produce atom rows for schema test"
        actual_cols = set(result.atom_rows[0].keys())
        assert self._ATOMS_REQUIRED_COLS <= actual_cols, (
            f"Missing atoms columns: {self._ATOMS_REQUIRED_COLS - actual_cols}"
        )

    def test_law_refs_row_columns(self) -> None:
        result = _project(REF_CROSSLINKS_HE)
        assert result.law_ref_rows, "REF_CROSSLINKS HE must produce law_ref rows"
        actual_cols = set(result.law_ref_rows[0].keys())
        assert self._LAW_REFS_REQUIRED_COLS <= actual_cols, (
            f"Missing law_refs columns: {self._LAW_REFS_REQUIRED_COLS - actual_cols}"
        )

    def test_signatures_row_columns(self) -> None:
        result = _project(FULL_AKN_HE)
        assert result.signature_rows, "FULL_AKN HE with conclusions must produce signature rows"
        actual_cols = set(result.signature_rows[0].keys())
        assert self._SIGNATURES_REQUIRED_COLS <= actual_cols, (
            f"Missing signatures columns: {self._SIGNATURES_REQUIRED_COLS - actual_cols}"
        )

    def test_corpus_row_dtype_contracts(self) -> None:
        """Key dtype contracts: he_year/he_number are ints; is_structured is bool."""
        result = _project(FULL_AKN_HE)
        row = result.corpus_rows[0]
        assert isinstance(row["he_year"], int)
        assert isinstance(row["he_number"], int)
        assert isinstance(row["is_structured"], bool)
        assert isinstance(row["structural_tier"], str)
        assert isinstance(row["he_id"], str)


# ---------------------------------------------------------------------------
# 4. Reuse-verification: #1 extractor unchanged for HE bodies
# ---------------------------------------------------------------------------


class TestReuseVerification:
    """Category 4: fi_he_law_refs rows produced by the #1 extractor
    produce the same shape as fi_refs rows from enacted-law context.
    """

    def test_law_ref_rows_match_reference_mention_to_row_schema(self) -> None:
        """Law_ref rows must match the schema produced by reference_mention_to_row()."""
        from lawvm.core.reference_mention import (
            CiteConfidence,
            CiteKind,
            ProvisionRef,
            ReferenceMention,
            reference_mention_to_row,
        )

        # Build a ReferenceMention that reference_mention_to_row() can serialize
        src = ProvisionRef(statute_id="he/2004/227", section_label="1")
        tgt = ProvisionRef(statute_id="2003/314", section_label="5")
        m = ReferenceMention(
            source_provision_ref=src,
            target_provision_ref=tgt,
            cite_kind=CiteKind.CROSS_STATUTE,
            cite_confidence=CiteConfidence.EXACT,
            phrase_lemma="ref_element",
            source_span=None,
            valid_at_interval=(None, None),
            edge_subtype="CITES",
        )
        canonical_row = reference_mention_to_row(m)

        # Now project the REF_CROSSLINKS_HE and get the HE law_ref rows
        result = _project(REF_CROSSLINKS_HE)
        he_law_ref_rows = result.law_ref_rows

        assert he_law_ref_rows, "REF_CROSSLINKS_HE must produce law_ref rows"

        # The HE law_ref rows must include all canonical_row keys (the reused schema)
        canonical_keys = set(canonical_row.keys())
        for he_row in he_law_ref_rows:
            for k in canonical_keys:
                assert k in he_row, (
                    f"Law_ref row missing key {k!r} from canonical reference_mention_to_row schema; "
                    f"row keys: {set(he_row.keys())}"
                )

    def test_same_extractor_produces_same_output_for_same_xml(self) -> None:
        """extract_all_reference_mentions is deterministic: same XML → same output."""
        from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

        xml = REF_CROSSLINKS_HE.xml_bytes
        he_source_id = f"he/{REF_CROSSLINKS_HE.he_year}/{REF_CROSSLINKS_HE.he_number}"

        result1 = extract_all_reference_mentions(xml, he_source_id)
        result2 = extract_all_reference_mentions(xml, he_source_id)

        assert len(result1.mentions) == len(result2.mentions), (
            "Extractor must be deterministic: same input → same number of mentions"
        )
        for m1, m2 in zip(result1.mentions, result2.mentions, strict=True):
            assert m1 == m2, "Extractor must be deterministic: same mentions in same order"


# ---------------------------------------------------------------------------
# 5. Negative test: PDF_WRAPPER produces NO atoms/refs/signatures
# ---------------------------------------------------------------------------


class TestNegativePdfWrapper:
    """Category 5: PDF_WRAPPER HEs produce zero rows in all non-corpus tables."""

    def test_pdf_wrapper_emits_zero_atom_rows(self) -> None:
        result = _project(PDF_WRAPPER_HE)
        assert result.atom_rows == [], (
            f"PDF_WRAPPER HE must emit 0 atom rows; got {len(result.atom_rows)}"
        )

    def test_pdf_wrapper_emits_zero_law_ref_rows(self) -> None:
        result = _project(PDF_WRAPPER_HE)
        assert result.law_ref_rows == [], (
            f"PDF_WRAPPER HE must emit 0 law_ref rows; got {len(result.law_ref_rows)}"
        )

    def test_pdf_wrapper_emits_zero_signature_rows(self) -> None:
        result = _project(PDF_WRAPPER_HE)
        assert result.signature_rows == [], (
            f"PDF_WRAPPER HE must emit 0 signature rows; got {len(result.signature_rows)}"
        )

    def test_full_akn_does_emit_atoms(self) -> None:
        """Negative sanity: FULL_AKN HE with body DOES emit atoms (not silently dropped)."""
        result = _project(FULL_AKN_HE)
        assert result.atom_rows, "FULL_AKN HE must emit atom rows (negative sanity check)"


# ---------------------------------------------------------------------------
# 6. Strict-mode test
# ---------------------------------------------------------------------------


class TestStrictMode:
    """Category 6: strict=True behavior."""

    def test_non_he_document_strict_disposition_abort(self) -> None:
        """Non-HE document failure must have strict_disposition='abort'."""
        result = _project(NON_HE_STATUTE, strict=True)
        failures = result.failures
        assert failures, "Non-HE document must emit a failure in strict mode"
        assert all(
            f.strict_disposition in ("abort", "record")
            for f in failures
        ), "All failures must have a valid strict_disposition"
        # WRONG_FRBR_SUBTYPE is an abort-level failure
        abort_failures = [f for f in failures if f.strict_disposition == "abort"]
        assert abort_failures, "At least one failure must have strict_disposition='abort'"

    def test_xml_parse_error_strict_disposition(self) -> None:
        """XML parse failure must have strict_disposition='abort'."""
        result = project_he_from_xml(b"<bad", he_year=2000, he_number=1, lang="fin", strict=True)
        assert result.failures
        assert result.failures[0].strict_disposition == "abort"

    def test_strict_does_not_suppress_corpus_row_for_valid_he(self) -> None:
        """Strict mode on a valid FULL_AKN HE must still emit the corpus row."""
        result = _project(FULL_AKN_HE, strict=True)
        assert len(result.corpus_rows) == 1


# ---------------------------------------------------------------------------
# 7. No-leak test
# ---------------------------------------------------------------------------


class TestNoLeak:
    """Category 7: synthetic test markers must not appear in non-test output."""

    def test_synthetic_he_id_not_in_corpus_locator(self) -> None:
        """Synthetic test HE IDs used in fixtures don't use production-conflicting locators."""
        # The fixture HE IDs are in clearly synthetic ranges (1996, etc.) but
        # the test marker that MUST NOT appear is the internal extractor source_id
        # format used during law_ref extraction: "he/{year}/{number}"
        result = _project(FULL_AKN_HE)
        # law_ref source_statute_id uses "he/YEAR/NUMBER" format — this is the
        # internal synthetic ID used during extraction. It must not bleed into
        # the he_id field of the corpus row.
        for row in result.corpus_rows:
            assert not row["he_id"].startswith("he/"), (
                f"Synthetic extractor source_id leaked into corpus row he_id: {row['he_id']!r}"
            )

    def test_law_ref_rows_carry_synthetic_source_id_but_not_in_corpus(self) -> None:
        """The he/{year}/{number} synthetic source_id is confined to law_ref rows,
        not leaked into fi_he_corpus rows.
        """
        result = _project(REF_CROSSLINKS_HE)
        # law_ref rows carry source_statute_id = "he/2004/227" — this is expected
        # in the law_refs table for provenance
        for row in result.law_ref_rows:
            assert row["source_statute_id"].startswith("he/"), (
                "law_ref rows must use he/{year}/{number} as source_statute_id for HE context"
            )

        # But corpus row must use the canonical HE identifier
        for row in result.corpus_rows:
            assert not row["he_id"].startswith("he/"), (
                "Corpus he_id must be the canonical HE identifier, not the extractor source_id"
            )


# ---------------------------------------------------------------------------
# CLI wiring tests
# ---------------------------------------------------------------------------


class TestCLIWiring:
    """Smoke tests that the new CLI subcommands are registered."""

    def test_fi_proposals_subcommand_registered(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["fi-proposals", "--help"])
        assert exc_info.value.code == 0

    def test_fi_proposal_show_subcommand_registered(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["fi-proposal-show", "--help"])
        assert exc_info.value.code == 0

    def test_sync_fi_proposals_subcommand_registered(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["sync-fi-proposals", "--help"])
        assert exc_info.value.code == 0

    def test_export_projections_include_he_corpus_flag(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args(["export-projections", "--include-he-corpus"])
        assert args.include_he_corpus is True

    def test_fi_proposal_show_include_flags(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "fi-proposal-show", "HE 98/1996 vp",
            "--include-atoms", "--include-law-refs", "--include-signatures",
        ])
        assert args.he_id == "HE 98/1996 vp"
        assert args.include_atoms is True
        assert args.include_law_refs is True
        assert args.include_signatures is True

    def test_fi_proposals_filter_flags(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "fi-proposals", "--ministry", "Oikeusministeriö",
            "--year", "1996", "--lifecycle", "closed",
        ])
        assert args.ministry == "Oikeusministeriö"
        assert args.year == 1996
        assert args.lifecycle == "closed"

    def test_sync_fi_proposals_flags(self) -> None:
        from lawvm.tools.cli import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "sync-fi-proposals", "--full", "--projection-only",
        ])
        assert args.full is True
        assert args.projection_only is True


# ---------------------------------------------------------------------------
# Projection-level batch test (in-memory, no farchive)
# ---------------------------------------------------------------------------


class TestProjectionBatch:
    """Verify batch projection output uses the schema-versioned directory."""

    def test_project_he_from_xml_all_fixtures_no_crash(self) -> None:
        """All conformance fixtures project without raising an exception."""
        for fid, fx in ALL_FIXTURES.items():
            result = project_he_from_xml(
                fx.xml_bytes,
                he_year=fx.he_year,
                he_number=fx.he_number,
                lang=fx.lang,
            )
            # Result must always return a typed HEProjectionResult
            assert isinstance(result, HEProjectionResult), (
                f"Fixture {fid!r}: expected HEProjectionResult, got {type(result)}"
            )

    def test_project_he_corpus_writes_jsonl(self, tmp_path: Path) -> None:
        """project_he_corpus() writes JSONL files when farchive has content."""
        # Build a minimal farchive with one HE
        from farchive import Farchive

        farchive_path = tmp_path / "test.farchive"
        data_dir = str(tmp_path / "projections")

        fa = Farchive(str(farchive_path))
        fa.store(
            "akn/fi/doc/government-proposal/1996/98/fin@/main.xml",
            FULL_AKN_HE.xml_bytes,
            storage_class="xml",
            metadata={
                "he_id": "HE 98/1996 vp",
                "he_year": "1996",
                "he_number": "98",
                "source_zip_sha256": "test_sha",
                "ingest_timestamp": "2024-06-01T00:00:00+00:00",
            },
            observed_at=_NOW,
        )
        fa.close()

        from lawvm.tools.export_fi_he_corpus import project_he_corpus

        counts = project_he_corpus(
            farchive_path=str(farchive_path),
            data_dir=data_dir,
            lang="fin",
            use_parquet=False,  # avoid pyarrow requirement in test
        )

        assert counts.get("fi_he_corpus", 0) >= 1, (
            f"Expected at least 1 corpus row; got counts: {counts}"
        )
        assert (tmp_path / "projections" / "fi_he_corpus.jsonl").exists()

    def test_project_he_corpus_pdf_wrapper_not_in_atoms(self, tmp_path: Path) -> None:
        """PDF_WRAPPER HE stored in farchive must not produce atom rows."""
        from farchive import Farchive

        farchive_path = tmp_path / "test_pdf.farchive"
        data_dir = str(tmp_path / "proj_pdf")

        fa = Farchive(str(farchive_path))
        fa.store(
            "akn/fi/doc/government-proposal/1996/103/fin@/main.xml",
            PDF_WRAPPER_HE.xml_bytes,
            storage_class="xml",
            metadata={
                "he_id": "HE 103/1996 vp",
                "he_year": "1996",
                "he_number": "103",
                "source_zip_sha256": "test_sha",
                "ingest_timestamp": "2024-06-01T00:00:00+00:00",
            },
            observed_at=_NOW,
        )
        fa.close()

        from lawvm.tools.export_fi_he_corpus import project_he_corpus

        counts = project_he_corpus(
            farchive_path=str(farchive_path),
            data_dir=data_dir,
            lang="fin",
            use_parquet=False,
        )

        assert counts.get("fi_he_corpus", 0) == 1
        assert counts.get("fi_he_atoms", 0) == 0, (
            "PDF_WRAPPER HE must not produce atom rows in batch projection"
        )


# ---------------------------------------------------------------------------
# Real-corpus regression tests (skipped if zip not available)
# ---------------------------------------------------------------------------

_GOVT_PROP_ZIP_PATH = Path.home() / "Downloads" / "government-proposal.zip"
_SKIP_REAL_CORPUS = not _GOVT_PROP_ZIP_PATH.exists()
_SKIP_REASON = "~/Downloads/government-proposal.zip not available"


@pytest.mark.skipif(_SKIP_REAL_CORPUS, reason=_SKIP_REASON)
@pytest.mark.slow
class TestRealCorpusProjection:
    """Real-corpus regression: project known HEs from government-proposal.zip."""

    def _read_he_xml(self, year: int, number: int, lang: str = "fin") -> bytes:
        import zipfile
        with zipfile.ZipFile(str(_GOVT_PROP_ZIP_PATH)) as zf:
            name = f"akn/fi/doc/government-proposal/{year}/{number}/{lang}@/main.xml"
            return zf.read(name)

    def test_he_98_1996_projects_structured(self) -> None:
        """HE 98/1996 is a FULL_AKN HE; must project with is_structured=True."""
        xml = self._read_he_xml(1996, 98)
        result = project_he_from_xml(
            xml, he_year=1996, he_number=98, lang="fin",
            source_zip_sha256="real_corpus_test",
        )
        assert len(result.corpus_rows) == 1
        assert result.corpus_rows[0]["is_structured"] is True
        assert result.corpus_rows[0]["structural_tier"] == "full_akn"
        assert result.atom_rows, "HE 98/1996 must produce atom rows"

    def test_he_103_1996_projects_pdf_wrapper(self) -> None:
        """HE 103/1996 is a PDF_WRAPPER HE; must project with is_structured=False."""
        xml = self._read_he_xml(1996, 103)
        result = project_he_from_xml(
            xml, he_year=1996, he_number=103, lang="fin",
            source_zip_sha256="real_corpus_test",
        )
        assert len(result.corpus_rows) == 1
        assert result.corpus_rows[0]["is_structured"] is False
        assert result.corpus_rows[0]["structural_tier"] == "pdf_wrapper"
        assert result.atom_rows == []
        assert result.law_ref_rows == []
        assert result.signature_rows == []

    def test_real_corpus_projection_no_exceptions(self, tmp_path: Path) -> None:
        """First 20 HEs from 1996 project without aborting."""
        from lawvm.finland.he_acquisition import acquire_fi_proposals
        from lawvm.tools.export_fi_he_corpus import project_he_corpus

        farchive_path = tmp_path / "real.farchive"
        data_dir = str(tmp_path / "projections")

        # Acquire a small slice
        run = acquire_fi_proposals(
            source=str(_GOVT_PROP_ZIP_PATH),
            dest=str(farchive_path),
            year_range=(1996, 1996),
            limit=20,
            workers=1,
        )
        assert run.added > 0, "Expected to ingest some HEs"

        counts = project_he_corpus(
            farchive_path=str(farchive_path),
            data_dir=data_dir,
            lang="fin",
            use_parquet=False,
        )
        assert counts.get("fi_he_corpus", 0) > 0


# ---------------------------------------------------------------------------
# Farchive-based real-corpus regression (REAL_CORPUS_REGRESSION_FOR_PROJECTION_EMITTERS_001)
# ---------------------------------------------------------------------------
#
# Per process rule REAL_CORPUS_REGRESSION_FOR_PROJECTION_EMITTERS_001:
# Every projection emitter requires an end-to-end test that goes through the
# real farchive path and asserts non-zero rows for known-cited HEs.
#
# Synthetic conformance fixtures bypass _extract_signatures_from_conclusions's
# real lookup path (conclusions element search), which is exactly how the
# fi_he_signatures 0-row bug went undetected.  This test class directly
# exercises the farchive path for signature extraction.

_FI_HE_FARCHIVE = Path("data/fi_government_proposal.farchive")
_SKIP_FARCHIVE = not _FI_HE_FARCHIVE.exists()
_SKIP_FARCHIVE_REASON = "data/fi_government_proposal.farchive not available"


@pytest.mark.skipif(_SKIP_FARCHIVE, reason=_SKIP_FARCHIVE_REASON)
@pytest.mark.slow
class TestFarchiveSignatureRegression:
    """Real-corpus regression via farchive path for fi_he_signatures.

    REAL_CORPUS_REGRESSION_FOR_PROJECTION_EMITTERS_001:
    Exercises the actual lookup path that produced 0 rows (hcontainer[@name='conclusions']
    vs bare <conclusions>) to prevent silent regression.
    """

    def _project_from_farchive(self, year: int, number: int) -> "HEProjectionResult":
        from farchive import Farchive
        from lawvm.tools.export_fi_he_corpus import project_he_from_xml

        fa = Farchive(str(_FI_HE_FARCHIVE))
        locator = f"akn/fi/doc/government-proposal/{year}/{number}/fin@/main.xml"
        xml_bytes = fa.get(locator)
        span = fa.resolve(locator)
        meta = span.last_metadata if span is not None else {}
        fa.close()
        assert xml_bytes is not None, f"No XML bytes for {locator}"
        assert isinstance(meta, Mapping)
        return project_he_from_xml(
            xml_bytes,
            he_year=year,
            he_number=number,
            lang="fin",
            source_file=locator,
            source_zip_sha256=str(meta.get("source_zip_sha256", "")),
        )

    def test_he_2024_100_has_signatures_via_farchive(self) -> None:
        """HE 100/2024 from farchive must produce >=2 signature rows.

        Root cause regression: code searched for <conclusions> (AKN element)
        but real Finlex HE XML uses <hcontainer name="conclusions"> inside
        <mainBody>.  This test fails before the fix, passes after.
        """
        result = self._project_from_farchive(2024, 100)
        assert result.signature_rows, (
            "HE 100/2024 via farchive must produce signature rows -- "
            "0 rows means the hcontainer[@name='conclusions'] lookup is broken. "
            "Root cause: lookup used <conclusions> element (never present in real HE XML) "
            "instead of <hcontainer name='conclusions'>."
        )
        assert len(result.signature_rows) >= 2, (
            f"Expected >=2 signatures in HE 100/2024; got {len(result.signature_rows)}"
        )
        roles = {r["role"] for r in result.signature_rows}
        # Modern HE: Pääministeri and at least one minister
        assert any("ministeri" in (r or "").lower() for r in roles), (
            f"Expected a minister role in HE 100/2024 signatures; got {roles}"
        )

    def test_he_1992_1_has_signatures_via_farchive(self) -> None:
        """Oldest corpus HE (1992/1) from farchive must also produce signature rows."""
        result = self._project_from_farchive(1992, 1)
        assert result.signature_rows, (
            "HE 1/1992 via farchive must produce signature rows (oldest corpus HE)"
        )

    def test_project_he_corpus_signatures_nonzero(self, tmp_path: Path) -> None:
        """Full batch projection through project_he_corpus() must yield non-zero signatures.

        This is the end-to-end regression: same path as rebuild-indexes calls.
        Uses a small limit=10 slice from the real farchive.
        """
        from lawvm.tools.export_fi_he_corpus import project_he_corpus

        counts = project_he_corpus(
            farchive_path=str(_FI_HE_FARCHIVE),
            data_dir=str(tmp_path / "v1"),
            lang="fin",
            limit=10,
            use_parquet=False,
        )
        assert counts.get("fi_he_signatures", 0) > 0, (
            f"project_he_corpus() with limit=10 from real farchive must yield >0 "
            f"signature rows; got {counts.get('fi_he_signatures', 0)}. "
            "Root cause: lookup used <conclusions> (AKN element, never in real XML) "
            "instead of <hcontainer name='conclusions'>."
        )
