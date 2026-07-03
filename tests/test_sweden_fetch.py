from __future__ import annotations
from typing_extensions import override
import sqlite3

from dataclasses import dataclass, field
import json
from typing import cast

import pytest

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
from lawvm.core import tree_ops
from lawvm.core.ir_helpers import ir_statute_from_dict
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.core.ir import (
    TextPatchKindEnum,
    StructuralAction,
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.sweden.fetch import (
    _ArchiveLike,
    _is_oracle_repeal_stub,
    _migrate_legacy_se_ir_blob,
    _normalize_compare_text,
    _se_oracle_version_relation,
    _se_parse_andring_inford_sfs,
    _reverse_patch_se_available_later_chain,
    se_three_bucket_for_classification,
    analyze_se_official_replay_feasibility,
    attach_official_artifacts_to_bundle,
    archive_se_source_bundle,
    build_se_source_bundle,
    check_se_official_replay,
    clean_se_pdf_text,
    compile_se_official_ops_to_archive,
    fetch_se_official_artifacts,
    fetch_se_rk_current_json,
    guess_se_legacy_pdf_url,
    guess_se_official_pdf_url,
    has_valid_se_official_pdf,
    hydrate_se_bundle_live,
    aggregate_se_official_coverage,
    enumerate_se_sfst_oracle_gain_bases,
    ingest_se_rk_current_from_sfst_archive,
    ingest_se_scraped_doc_html_map,
    parse_se_sfst_html_to_rk_current,
    scaled_ingest_se_sfst_oracles,
    load_se_bundle_from_archive,
    load_se_current_ir_from_archive,
    load_se_backfill_official_history_from_archive,
    load_se_official_act_from_archive,
    load_se_official_base_ir_from_archive,
    load_se_official_ops_adjudications_from_archive,
    load_se_official_clause_surface_from_archive,
    load_se_official_elaboration_from_archive,
    load_se_official_effects_plan_from_archive,
    load_se_official_ops_from_archive,
    load_se_official_payload_surface_from_archive,
    load_se_source_record_from_archive,
    parse_se_rk_issue_date,
    parse_se_legacy_pdf_url,
    plan_se_older_base_rebuild,
    probe_se_public_source_status,
    rebuild_se_older_base_from_official_chain,
    scan_se_official_replay_act,
    search_se_legacy_pdf_url,
    se_official_act_locator,
    se_official_base_ir_locator,
    se_official_clause_surface_locator,
    se_official_elaboration_locator,
    se_official_effects_plan_locator,
    se_official_ops_locator,
    se_official_ops_adjudications_locator,
    se_official_payload_surface_locator,
    se_rk_current_url,
    se_bundle_manifest_locator,
    se_backfill_official_history_locator,
    se_official_doc_locator,
    se_official_pdf_locator,
    se_current_ir_locator,
    se_pdf_cleanup_locator,
    se_pdf_text_locator,
    se_rk_current_json_locator,
    se_source_record_locator,
)
from lawvm.sweden.grafter import (
    compile_se_official_act_ops,
    build_se_official_base_statute,
    _build_se_official_elaboration,
    _build_se_official_clause_surface,
    _build_se_official_effects_plan,
    _build_se_official_payload_surface,
    _coerce_official_act,
    _lower_se_official_effects_plan,
    parse_se_official_act_text,
    se_official_act_text_to_dict,
    se_legal_operation_from_dict,
    se_legal_operation_to_dict,
    SEOfficialClauseSurface,
    SEOfficialElaboratedIntent,
    SEOfficialEffectsPlan,
    SEOfficialEffectPlanItem,
    SEOfficialPayloadSurface,
    extract_se_expiry_boundary_date,
    se_op_is_temporal_expiry,
    se_op_tombstone_disposition,
    se_temporal_expiry_event,
    se_temporal_events_for_ops,
)
from lawvm.sweden.grafter import (
    apply_se_ops,
    apply_se_ops_conserved,
    canonicalize_se_table_section_text,
    extract_se_current_section_texts,
    materialize_se_statute_as_of,
    parse_se_statute,
    se_appendix_text_map,
    se_heading_before_section_map,
    se_section_text_map,
    se_statute_invariant_violation_records,
    se_statute_invariant_violations,
)


def test_se_compare_text_normalization_uses_named_presentation_projection() -> None:
    assert (
        _normalize_compare_text("1 Exempel – text 1. med nummer Förordning (2026:280).")
        == "Exempel - text med nummer"
    )


def test_se_compare_text_normalization_folds_trailing_lag_attribution() -> None:
    # The consolidated RK surface tags an amended provision with the amending
    # act's own short citation; the replay payload renders it without that tag.
    assert (
        _normalize_compare_text("Avgiften ska betalas senast den 1 mars. Lag (2018:221).")
        == _normalize_compare_text("Avgiften ska betalas senast den 1 mars.")
        == "Avgiften ska betalas senast den 1 mars."
    )


def test_se_compare_text_normalization_keeps_inline_lag_reference() -> None:
    # A "Lag (YYYY:N)" reference embedded INSIDE the operative body (not a
    # trailing provenance tag) is substantive and must not be folded away.
    assert (
        _normalize_compare_text("Bestämmelserna i Lag (2018:221) ska tillämpas på avgiften.")
        == "Bestämmelserna i Lag (2018:221) ska tillämpas på avgiften."
    )


def test_se_compare_text_normalization_folds_trailing_prop_provenance() -> None:
    # Editorial preparatory-work citations ("Prop." / "Jfr prop.") trailing the
    # operative text are provenance, not part of the provision.
    canonical = "Beslutet ska fattas av regeringen."
    assert _normalize_compare_text(f"{canonical} Prop. 2001/02:1.") == canonical
    assert _normalize_compare_text(f"{canonical} Jfr prop. 1999/2000:23.") == canonical


def test_se_compare_text_normalization_keeps_substantive_prop_reference() -> None:
    # A reference to a proposition INSIDE body text is substantive and stays.
    text = "Vad som sägs i prop. 2001/02:1 om detta ska beaktas vid tillämpningen."
    assert _normalize_compare_text(text) == text


def test_se_compare_text_normalization_folds_list_enumerator_case() -> None:
    # A consolidated surface may render an alphabetic list-item label with a
    # different case or with/without a leading space; fold the enumerator token.
    assert (
        _normalize_compare_text("A) första punkten")
        == _normalize_compare_text("a) första punkten")
        == "a) första punkten"
    )


def test_se_compare_text_normalization_list_enumerator_keeps_body_diff() -> None:
    # Folding the enumerator label must NOT make genuinely different list-item
    # bodies compare equal.
    assert _normalize_compare_text("a) första punkten") != _normalize_compare_text(
        "a) andra punkten"
    )


def test_se_parse_andring_inford_sfs_extracts_or_returns_none() -> None:
    assert _se_parse_andring_inford_sfs("Ändring införd: t.o.m. SFS 2030:1") == "2030:1"
    assert _se_parse_andring_inford_sfs("t.o.m. SFS 2018:221a") == "2018:221a"
    # Missing/unparseable stamps must return None so callers classify as unknown.
    assert _se_parse_andring_inford_sfs(None) is None
    assert _se_parse_andring_inford_sfs("") is None
    assert _se_parse_andring_inford_sfs("Grundförfattning") is None


def test_se_oracle_version_relation_compares_stamp_against_replay() -> None:
    # Strictly-later oracle stamp -> dating artifact (correct replay, later doc).
    assert _se_oracle_version_relation("2026:286", "2030:1") == "later"
    # Contemporaneous/older stamp -> genuine surface drift.
    assert _se_oracle_version_relation("2026:286", "2026:286") == "same_or_earlier"
    assert _se_oracle_version_relation("2026:286", "2020:5") == "same_or_earlier"
    # Missing or malformed ids -> the relation cannot be trusted.
    assert _se_oracle_version_relation("2026:286", None) == "unknown"
    assert _se_oracle_version_relation("not-an-sfs", "2030:1") == "unknown"


def test_se_three_bucket_later_oracle_stamp_is_version_mismatch() -> None:
    # A strictly-later consolidation stamp moves an oracle-fallback row out of
    # "drift" into the honest oracle_version_mismatch bucket.
    assert (
        se_three_bucket_for_classification(
            "official_oracle_version_mismatch", matched=True
        )
        == "oracle_version_mismatch"
    )


def test_se_three_bucket_same_or_earlier_oracle_stamp_is_genuine_mismatch() -> None:
    # A contemporaneous/older stamp is a real current-surface drift.
    assert (
        se_three_bucket_for_classification(
            "official_oracle_match_current_surface_drift", matched=True
        )
        == "genuine_mismatch"
    )


def test_se_three_bucket_genuine_and_editorial_match_count_as_match() -> None:
    # Genuine content equality and editorial-only presentation differences both
    # count as genuine_match (the latter differ only in presentation).
    assert se_three_bucket_for_classification("exact", matched=True) == "genuine_match"
    assert (
        se_three_bucket_for_classification("inline_numbering_only", matched=True)
        == "genuine_match"
    )


def test_se_three_bucket_unknown_and_content_mismatch_classified_honestly() -> None:
    # An untrustworthy stamp is "unknown"; a genuine content disagreement is
    # genuine_mismatch.
    assert (
        se_three_bucket_for_classification(
            "official_oracle_match_version_unknown", matched=True
        )
        == "unknown"
    )
    assert (
        se_three_bucket_for_classification("content_mismatch", matched=False)
        == "genuine_mismatch"
    )


@dataclass
class _FakeArchive(_ArchiveLike):
    fetched: dict[str, bytes] = field(default_factory=dict)
    stored: dict[str, bytes] = field(default_factory=dict)
    fetch_calls: list[tuple[str, str, float]] = field(default_factory=list)

    def fetch(
        self, url: str, max_age_hours: float = 168.0, headers: dict | None = None, content_type: str = "auto"
    ) -> bytes | None:
        self.fetch_calls.append((url, content_type, max_age_hours))
        return self.fetched.get(url)

    @override
    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:
        self.stored[locator] = data
        return "fakehash"

    @override
    def get(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    def get_latest(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    @override
    def has(self, locator: str, *, max_age_hours: float = float("inf")) -> bool:
        return locator in self.stored

    def is_fresh(self, locator: str, max_age_hours: float) -> bool:
        return locator in self.stored

    def locators(self, pattern: str = "%") -> list[str]:
        return [k for k in self.stored if pattern.replace("%", "") in k]


@dataclass
class _ReadonlyFakeArchive(_FakeArchive):
    """In-memory fake that mimics a real readonly :class:`farchive.Farchive`.

    Mirrors the failure shape of the coverage-scan worker: ``store`` raises
    ``sqlite3.OperationalError`` exactly as a SQLite-backed Farchive does when
    opened with ``readonly=True``. The ``_readonly`` attribute is exposed so
    production helpers that probe writability before attempting a write
    (``_se_archive_is_writable``) can exercise the same branch against the fake.
    """

    _readonly: bool = True
    attempted_writes: list[tuple[str, bytes, str | None]] = field(default_factory=list)

    @override
    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:
        self.attempted_writes.append((locator, data, storage_class))
        raise sqlite3.OperationalError("attempt to write a readonly database")


@pytest.fixture(autouse=True)
def _disable_sweden_fetch_retry_sleep(monkeypatch) -> None:
    monkeypatch.setattr("lawvm.sweden.fetch.time.sleep", lambda seconds: None)


def _se_appendix_supplement_blob() -> dict[str, object]:
    """A bare SE IR statute payload carrying one appendix supplement node."""
    return {
        "statute_id": "2015:284",
        "title": "Testlag",
        "body": {
            "kind": "body",
            "children": [{"kind": "section", "label": "1", "text": "Text."}],
        },
        "metadata": {},
        "supplements": [
            {
                "kind": "appendix",
                "label": "1",
                "text": "Bilaga",
                "attrs": {},
                "children": [{"kind": "heading", "text": "Rubrik", "attrs": {}}],
            }
        ],
    }


def test_migrate_legacy_se_ir_blob_renames_schedules_to_supplements() -> None:
    current = _se_appendix_supplement_blob()
    legacy = dict(current)
    legacy["schedules"] = legacy.pop("supplements")

    migrated = _migrate_legacy_se_ir_blob(legacy)

    assert "schedules" not in migrated
    assert migrated["supplements"] == current["supplements"]
    # Faithful rename: nothing else changed, no data dropped.
    assert {k: v for k, v in migrated.items() if k != "supplements"} == {
        k: v for k, v in current.items() if k != "supplements"
    }
    # The legacy supplement node survives deserialization into core.
    statute = ir_statute_from_dict(migrated)
    assert len(statute.supplements) == 1
    assert statute.supplements[0].kind.value == "appendix"


def test_migrate_legacy_se_ir_blob_passes_through_current_supplements() -> None:
    current = _se_appendix_supplement_blob()

    migrated = _migrate_legacy_se_ir_blob(current)

    assert migrated is current
    statute = ir_statute_from_dict(migrated)
    assert len(statute.supplements) == 1


def test_migrate_legacy_se_ir_blob_refuses_ambiguous_both_keys() -> None:
    ambiguous = _se_appendix_supplement_blob()
    ambiguous["schedules"] = []

    with pytest.raises(ValueError, match="both legacy 'schedules'"):
        _migrate_legacy_se_ir_blob(ambiguous)


def test_load_se_official_base_ir_migrates_legacy_schedules_on_read() -> None:
    legacy = _se_appendix_supplement_blob()
    legacy["schedules"] = legacy.pop("supplements")
    archive = _FakeArchive(
        stored={se_official_base_ir_locator("2015:284"): json.dumps(legacy).encode("utf-8")}
    )

    blob = load_se_official_base_ir_from_archive(archive, "2015:284")
    assert blob is not None
    assert "schedules" not in blob
    # The blob now feeds core's bare-statute deserializer without rejection.
    statute = ir_statute_from_dict(blob)
    assert len(statute.supplements) == 1
    assert statute.supplements[0].label == "1"


def test_load_se_current_ir_passes_through_modern_supplements_blob() -> None:
    current = _se_appendix_supplement_blob()
    archive = _FakeArchive(
        stored={se_current_ir_locator("2015:284"): json.dumps(current).encode("utf-8")}
    )

    blob = load_se_current_ir_from_archive(archive, "2015:284")
    assert blob is not None
    assert "schedules" not in blob
    assert blob["supplements"] == current["supplements"]


def test_se_statute_invariant_violations_include_typed_records() -> None:
    statute = IRStatute(
        statute_id="se-test",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Second."),
                IRNode(kind=IRNodeKind.SECTION, label="1", text="First."),
            ),
        ),
    )

    records = se_statute_invariant_violation_records(statute)
    replayed = apply_se_ops(statute, [])

    assert se_statute_invariant_violations(statute) == ["body:body: section out of order: 2 > 1"]
    assert records[0].to_dict()["kind"] == "sort_order"
    assert records[0].to_dict()["path"] == "body"
    assert records[0].to_dict()["previous_label"] == "2"
    assert replayed.metadata["typed_invariant_violations"][0]["kind"] == "sort_order"


def test_se_expected_invariant_tolerances_are_filtered_from_typed_records() -> None:
    statute = IRStatute(
        statute_id="se-test",
        title="",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.HEADING, text="Rubrik"),
                IRNode(kind=IRNodeKind.SECTION, label="1", text="Text."),
            ),
        ),
    )

    assert se_statute_invariant_violation_records(statute) == []
    assert se_statute_invariant_violations(statute) == []


def test_fetch_se_official_artifacts_fetches_doc_and_pdf_and_stores_text(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")

    bundle = fetch_se_official_artifacts("2026:286", archive, diagnostics_out=diagnostics)

    assert bundle is not None
    assert diagnostics == []
    assert bundle.doc_url == doc_url
    assert bundle.doc_locator == se_official_doc_locator("2026:286")
    assert bundle.pdf_url == pdf_url
    assert bundle.pdf_locator == se_official_pdf_locator("2026:286")
    assert bundle.pdf_text_url == se_pdf_text_locator("2026:286")
    assert bundle.pdf_cleaned_text_url == se_pdf_cleanup_locator("2026:286")
    assert archive.stored[bundle.pdf_text_url] == b"Recovered PDF text"
    assert bundle.doc_locator in archive.stored
    assert bundle.pdf_locator in archive.stored
    assert bundle.pdf_cleaned_text_url in archive.stored
    assert se_official_act_locator("2026:286") in archive.stored
    assert archive.fetch_calls == [
        (doc_url, "html", float("inf")),
        (pdf_url, "pdf", float("inf")),
    ]


def test_fetch_se_official_artifacts_retries_transient_doc_failures(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive()
    attempts = {"doc": 0, "pdf": 0}

    def flaky_fetch(
        url: str, max_age_hours: float = 168.0, headers: dict | None = None, content_type: str = "auto"
    ) -> bytes | None:
        archive.fetch_calls.append((url, content_type, max_age_hours))
        if url == doc_url:
            attempts["doc"] += 1
            if attempts["doc"] < 3:
                return None
            return b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>'
        if url == pdf_url:
            attempts["pdf"] += 1
            return b"%PDF-1.7 fake"
        return None

    monkeypatch.setattr(archive, "fetch", flaky_fetch)
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")
    monkeypatch.setattr("lawvm.sweden.fetch.time.sleep", lambda seconds: None)

    bundle = fetch_se_official_artifacts("2026:286", archive)

    assert bundle is not None
    assert attempts["doc"] == 3
    assert attempts["pdf"] == 1
    assert archive.fetch_calls[:3] == [
        (doc_url, "html", float("inf")),
        (doc_url, "html", float("inf")),
        (doc_url, "html", float("inf")),
    ]
    assert archive.fetch_calls[3] == (pdf_url, "pdf", float("inf"))


def test_fetch_se_official_artifacts_reuses_existing_pdf_text(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    text_url = se_pdf_text_locator("2026:286")
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        },
        stored={text_url: b"Existing text"},
    )

    def fail_if_called(pdf_bytes: bytes) -> str:
        raise AssertionError("pdftotext should not run when extracted text is already archived")

    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", fail_if_called)
    bundle = fetch_se_official_artifacts("2026:286", archive)

    assert bundle is not None
    assert archive.stored[text_url] == b"Existing text"


def test_fetch_official_does_not_mirror_blocked_doc_html_when_using_override(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b"<html><title>Attention Required! | Cloudflare</title></html>",
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")

    bundle = fetch_se_official_artifacts("2026:286", archive, pdf_url_override=pdf_url)

    assert bundle is not None
    assert bundle.pdf_url == pdf_url
    assert se_official_doc_locator("2026:286") not in archive.stored


def test_fetch_official_falls_back_to_month_probe_when_doc_blocked_and_rk_issue_missing(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026063.html"
    rk_url = se_rk_current_url("2026:63")
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-02/SFS2026-63.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b"<html><title>Attention Required! | Cloudflare</title></html>",
            rk_url: b"<html><body>generic RK page without issue date</body></html>",
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")

    bundle = fetch_se_official_artifacts("2026:63", archive)

    assert bundle is not None
    assert bundle.pdf_url == pdf_url
    assert se_official_pdf_locator("2026:63") in archive.stored
    assert se_official_doc_locator("2026:63") not in archive.stored


def test_fetch_official_records_diagnostic_when_pdf_unavailable(monkeypatch) -> None:
    archive = _FakeArchive()
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr("lawvm.sweden.fetch.time.sleep", lambda seconds: None)
    monkeypatch.setattr("lawvm.sweden.fetch.search_se_legacy_pdf_url", lambda sfs_id: None)

    bundle = fetch_se_official_artifacts("2026:286", archive, diagnostics_out=diagnostics)

    assert bundle is None
    assert diagnostics == [
        {
            "rule_id": "se_official_artifacts_unavailable",
            "family": "source_pathology",
            "phase": "acquisition",
            "reason": "Sweden official SFS PDF artifact could not be located or fetched",
            "sfs_id": "2026:286",
            "locator": se_official_pdf_locator("2026:286"),
            "doc_url": "https://svenskforfattningssamling.se/doc/2026286.html",
            "pdf_url": "",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "pdf_source_attempts": (
                {
                    "lane": "legacy_direct_guess",
                    "url": "https://rkrattsdb.gov.se/SFSdoc/26/260286.PDF",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "legacy_search_result",
                    "url": "https://rkrattsdb.gov.se/sfspdf/sql_search_rsp.asp",
                    "lane_attempt_status": "no_result",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-01/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-02/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-04/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-05/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-06/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-07/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-08/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-09/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-10/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-11/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
                {
                    "lane": "official_month_probe",
                    "url": "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-12/SFS2026-286.pdf",
                    "lane_attempt_status": "missing_or_non_pdf",
                },
            ),
        }
    ]


def test_fetch_se_official_artifacts_records_pdf_text_extraction_failure(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: None)

    bundle = fetch_se_official_artifacts("2026:286", archive, diagnostics_out=diagnostics)

    assert bundle is not None
    assert se_official_pdf_locator("2026:286") in archive.stored
    assert se_pdf_text_locator("2026:286") not in archive.stored
    assert se_pdf_cleanup_locator("2026:286") not in archive.stored
    assert diagnostics == [
        {
            "rule_id": "se_official_pdf_text_extraction_failed",
            "family": "source_pathology",
            "phase": "extraction",
            "reason": "Sweden official SFS PDF was fetched but text extraction produced no payload",
            "sfs_id": "2026:286",
            "locator": se_pdf_text_locator("2026:286"),
            "doc_url": doc_url,
            "pdf_url": pdf_url,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_fetch_se_official_artifacts_records_base_ir_build_failure(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    diagnostics: list[dict[str, object]] = []

    class _ParsedAct:
        is_amending_act = False

    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")
    monkeypatch.setattr(
        "lawvm.sweden.fetch.parse_se_official_act_text",
        lambda text, *, sfs_id: _ParsedAct(),
    )
    monkeypatch.setattr(
        "lawvm.sweden.fetch.se_official_act_text_to_dict",
        lambda act: {"sfs_id": "2026:286", "is_amending_act": False},
    )

    def fail_base_ir_build(act, *, statute_id: str = ""):
        raise ValueError("missing provisions")

    monkeypatch.setattr("lawvm.sweden.fetch.build_se_official_base_statute", fail_base_ir_build)

    bundle = fetch_se_official_artifacts("2026:286", archive, diagnostics_out=diagnostics)

    assert bundle is not None
    assert se_official_act_locator("2026:286") in archive.stored
    assert se_official_base_ir_locator("2026:286") not in archive.stored
    assert diagnostics == [
        {
            "rule_id": "se_official_base_ir_build_failed",
            "family": "source_pathology",
            "phase": "extraction",
            "reason": "Sweden official act text was parsed but base IR construction failed",
            "sfs_id": "2026:286",
            "locator": se_official_base_ir_locator("2026:286"),
            "doc_url": doc_url,
            "pdf_url": pdf_url,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "exception_type": "ValueError",
        }
    ]


def test_guess_and_parse_legacy_sfspdf_urls() -> None:
    assert guess_se_legacy_pdf_url("2015:284") == "https://rkrattsdb.gov.se/SFSdoc/15/150284.PDF"
    assert guess_se_legacy_pdf_url("2018:11") == "https://rkrattsdb.gov.se/SFSdoc/18/180011.PDF"
    html = b'<a href="/SFSdoc/15/150284.PDF" target="_blank">SFS 2015:284</a>'
    assert parse_se_legacy_pdf_url(html) == "https://rkrattsdb.gov.se/SFSdoc/15/150284.PDF"


def test_search_se_legacy_pdf_url_posts_real_form_shape(monkeypatch) -> None:
    posted: dict[str, str] = {}

    def fake_post(url: str, *, payload: dict[str, str]) -> bytes:
        posted.update(payload)
        return b'<a href="/SFSdoc/15/150284.PDF">SFS 2015:284</a>'

    monkeypatch.setattr("lawvm.sweden.fetch._curl_form_post", fake_post)

    result = search_se_legacy_pdf_url("2015:284")

    assert result == "https://rkrattsdb.gov.se/SFSdoc/15/150284.PDF"
    assert posted == {
        "SFS_nr": "2015:284",
        "title": "",
        "author": "",
        "departement": "",
        "ACTION": "  Sök  ",
    }


def test_fetch_official_falls_back_to_legacy_sfspdf_direct_url(monkeypatch) -> None:
    legacy_pdf_url = "https://rkrattsdb.gov.se/SFSdoc/15/150284.PDF"
    archive = _FakeArchive(
        fetched={
            legacy_pdf_url: b"%PDF-1.7 legacy",
        }
    )
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr("lawvm.sweden.fetch.time.sleep", lambda s: None)
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered legacy PDF text")
    monkeypatch.setattr("lawvm.sweden.fetch.search_se_legacy_pdf_url", lambda sfs_id: None)

    bundle = fetch_se_official_artifacts("2015:284", archive, diagnostics_out=diagnostics)

    assert bundle is not None
    assert bundle.doc_url == "https://rkrattsdb.gov.se/sfspdf/"
    assert bundle.pdf_url == legacy_pdf_url
    assert archive.stored[se_official_pdf_locator("2015:284")] == b"%PDF-1.7 legacy"
    assert diagnostics == [
        {
            "rule_id": "se_official_pdf_source_lane_fallback",
            "family": "source_lane_selection",
            "phase": "acquisition",
            "reason": "Sweden official SFS PDF was recovered through a fallback source lane",
            "sfs_id": "2015:284",
            "locator": se_official_pdf_locator("2015:284"),
            "doc_url": "https://rkrattsdb.gov.se/sfspdf/",
            "pdf_url": legacy_pdf_url,
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
            "selected_source_lane": "legacy_direct_guess",
            "selected_source_locator": legacy_pdf_url,
            "source_lane_attempts": (
                {
                    "lane": "legacy_direct_guess",
                    "lane_attempt_status": "valid_pdf",
                    "locator": legacy_pdf_url,
                },
            ),
            "doc_status": "missing",
            "selected_pdf_lane": "legacy_direct_guess",
            "pdf_source_attempts": (
                {
                    "lane": "legacy_direct_guess",
                    "url": legacy_pdf_url,
                    "lane_attempt_status": "valid_pdf",
                },
            ),
        }
    ]


def test_has_valid_se_official_pdf_checks_magic_header() -> None:
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/official.pdf": b"HTTP 404 Not Found",
            "se://sfs/2025:399/official.pdf": b"%PDF-1.7 fake",
        }
    )

    assert has_valid_se_official_pdf(archive, "2015:284") is False
    assert has_valid_se_official_pdf(archive, "2025:399") is True


def test_probe_se_public_source_status_classifies_cloudflare_blocked_doc(monkeypatch) -> None:
    def fake_probe(url: str, *, byte_range: str | None = None) -> tuple[int | None, bytes]:
        if "doc/" in url:
            return (403, b"<html><title>Attention Required! | Cloudflare</title></html>")
        return (404, b"not found")

    monkeypatch.setattr("lawvm.sweden.fetch._curl_probe_bytes", fake_probe)

    result = probe_se_public_source_status("2015:284")

    assert result["doc_status"] == "cloudflare_blocked"
    assert result["pdf_status"] == "not_found"
    assert result["public_source_viable"] is False


def test_sweden_text_locators_are_stable() -> None:
    assert se_bundle_manifest_locator("2026:286") == "se://sfs/2026:286/bundle.json"
    assert se_official_act_locator("2026:286") == "se://sfs/2026:286/official.act.json"
    assert se_official_doc_locator("2026:286") == "se://sfs/2026:286/official.doc.html"
    assert se_official_pdf_locator("2026:286") == "se://sfs/2026:286/official.pdf"
    assert se_pdf_text_locator("2026:286") == "se://sfs/2026:286/official.pdf.txt"
    assert se_pdf_cleanup_locator("2026:286") == "se://sfs/2026:286/official.cleaned.txt"


_SE_SFST_REAL_PAGE = (
    "<!DOCTYPE html><html><body>"
    '<div class="result-inner-box bold">\r\n SFS-nummer \xc2\xb7 2015:284 \xc2\xb7</div>'
    '<div class="result-inner-box">'
    '<span class="bold">F\xf6rordning (2015:284) med instruktion f\xf6r Testmyndigheten</span>'
    "</div>"
    '<div class="result-inner-box"><span class="bold">Utf\xe4rdad:</span> 2015-05-21</div>'
    '<div class="result-inner-box">'
    '<span class="bold">\xc4ndring inf\xf6rd:</span> t.o.m. SFS 2026:280</div>'
    '<div class="result-box-text body-text">'
    "Uppgifter<br><br>1 \xa7 F\xf6rsta paragrafen.<br>"
    "Forts\xe4ttning. Lag (2023:404).<br><br>"
    "2 \xa7 Andra paragrafen.<br></div>"
    "</body></html>"
)

_SE_SFST_EMPTY_PAGE = (
    "<!DOCTYPE html><html><body>"
    '<div class="search-hits">Totalt 0 tr\xe4ffar</div>'
    "<div>Inga tr\xe4ffar</div></body></html>"
)


def test_parse_se_sfst_html_lifts_real_consolidated_page_to_rk_document() -> None:
    document = parse_se_sfst_html_to_rk_current(_SE_SFST_REAL_PAGE, "2015:284")

    assert document is not None
    assert document["beteckning"] == "2015:284"
    assert document["rubrik"] == "Förordning (2015:284) med instruktion för Testmyndigheten"
    assert document["publicerad"] is True
    fulltext = document["fulltext"]
    assert fulltext["utfardadDateTime"] == "2015-05-21T00:00:00"
    assert fulltext["andringInford"] == "t.o.m. SFS 2026:280"
    assert "1 § Första paragrafen." in fulltext["forfattningstext"]
    assert "2 § Andra paragrafen." in fulltext["forfattningstext"]
    assert "Lag (2023:404)." in fulltext["forfattningstext"]


def test_parse_se_sfst_html_returns_none_for_empty_search_page() -> None:
    assert parse_se_sfst_html_to_rk_current(_SE_SFST_EMPTY_PAGE, "2002:1896") is None


def test_parse_se_sfst_html_round_trips_through_current_text_parser() -> None:
    from lawvm.sweden.grafter import parse_se_statute, se_section_text_map

    document = parse_se_sfst_html_to_rk_current(_SE_SFST_REAL_PAGE, "2015:284")
    assert document is not None
    statute = parse_se_statute(document, statute_id="2015:284")
    section_text = se_section_text_map(statute)
    assert section_text["1"].startswith("Första paragrafen.")
    assert section_text["2"] == "Andra paragrafen."


def test_ingest_se_rk_current_from_sfst_archive_seeds_oracle_bundle() -> None:
    archive = _FakeArchive()
    archive.stored[se_rk_current_url("2015:284")] = _SE_SFST_REAL_PAGE.encode("utf-8")

    assert ingest_se_rk_current_from_sfst_archive(archive, "2015:284") is True
    assert archive.has(se_rk_current_json_locator("2015:284"))
    assert archive.has(se_current_ir_locator("2015:284"))
    assert archive.has(se_source_record_locator("2015:284"))
    assert archive.has(se_bundle_manifest_locator("2015:284"))


def test_ingest_se_rk_current_from_sfst_archive_skips_empty_or_missing_pages() -> None:
    archive = _FakeArchive()
    # No archived sfst page at all.
    assert ingest_se_rk_current_from_sfst_archive(archive, "2002:1896") is False
    # An archived but empty (0-hit) search page.
    archive.stored[se_rk_current_url("2002:1896")] = _SE_SFST_EMPTY_PAGE.encode("utf-8")
    assert ingest_se_rk_current_from_sfst_archive(archive, "2002:1896") is False
    assert not archive.has(se_rk_current_json_locator("2002:1896"))


class _GlobArchive(_FakeArchive):
    """A fake archive whose ``locators`` honours SQL-LIKE ``%`` wildcards.

    The base :class:`_FakeArchive` does a naive substring match which cannot
    distinguish ``se://sfs/%/official.ops.json`` from other locators, so the
    scaled-ingest enumeration (which globs the ops locators) needs a sharper
    matcher to exercise the real gain-base selection.
    """

    @override
    def locators(self, pattern: str = "%") -> list[str]:
        import re as _re

        regex = "^" + "".join(
            ".*" if part == "%" else _re.escape(part)
            for part in _re.split(r"(%)", pattern)
        ) + "$"
        compiled = _re.compile(regex)
        return [k for k in self.stored if compiled.fullmatch(k)]


def _seed_se_amending_act(
    archive: _GlobArchive, amending_sfs_id: str, base_sfs_id: str
) -> None:
    """Seed a minimal compiled amending act that targets ``base_sfs_id``."""
    archive.stored[se_official_ops_locator(amending_sfs_id)] = json.dumps(
        {"ops": []}
    ).encode("utf-8")
    archive.stored[se_official_act_locator(amending_sfs_id)] = json.dumps(
        {"sfs_id": amending_sfs_id, "amended_act_sfs_id": base_sfs_id}
    ).encode("utf-8")


def test_enumerate_se_sfst_oracle_gain_bases_classifies_each_base() -> None:
    archive = _GlobArchive()
    # base 2015:284 -> has a real sfst page, no oracle yet => GAIN
    _seed_se_amending_act(archive, "2018:111", "2015:284")
    archive.stored[se_rk_current_url("2015:284")] = _SE_SFST_REAL_PAGE.encode("utf-8")
    # base 2002:1896 -> archived sfst page but EMPTY 0-träffar => skip
    _seed_se_amending_act(archive, "2018:222", "2002:1896")
    archive.stored[se_rk_current_url("2002:1896")] = _SE_SFST_EMPTY_PAGE.encode("utf-8")
    # base 2010:900 -> already carries an oracle => protected, never a gain
    _seed_se_amending_act(archive, "2018:333", "2010:900")
    archive.stored[se_rk_current_json_locator("2010:900")] = b"{}"
    # base 2011:111 -> no archived sfst page at all => skip
    _seed_se_amending_act(archive, "2018:444", "2011:111")

    plan = enumerate_se_sfst_oracle_gain_bases(archive)

    assert plan["amending_acts_with_ops"] == 4
    assert plan["distinct_bases_targeted"] == 4
    assert plan["gain_bases"] == ["2015:284"]
    assert plan["gain_base_count"] == 1
    assert plan["already_oracle_bases"] == ["2010:900"]
    assert plan["empty_sfst_page_bases"] == ["2002:1896"]
    assert plan["no_sfst_page_bases"] == ["2011:111"]


def test_scaled_ingest_se_sfst_oracles_adds_real_skips_empty_and_existing() -> None:
    archive = _GlobArchive()
    # real gain base
    _seed_se_amending_act(archive, "2018:111", "2015:284")
    archive.stored[se_rk_current_url("2015:284")] = _SE_SFST_REAL_PAGE.encode("utf-8")
    # empty page base — must never be written
    _seed_se_amending_act(archive, "2018:222", "2002:1896")
    archive.stored[se_rk_current_url("2002:1896")] = _SE_SFST_EMPTY_PAGE.encode("utf-8")
    # protected existing oracle (stands in for the real RK-API blobs)
    _seed_se_amending_act(archive, "2018:333", "2010:900")
    sentinel = b'{"_protected": true}'
    archive.stored[se_rk_current_json_locator("2010:900")] = sentinel

    result = scaled_ingest_se_sfst_oracles(archive)

    assert result["added_bases"] == ["2015:284"]
    assert result["added_count"] == 1
    assert result["failed_count"] == 0
    # The real RK blob is untouched.
    assert archive.stored[se_rk_current_json_locator("2010:900")] == sentinel
    # The empty page never produced an oracle.
    assert not archive.has(se_rk_current_json_locator("2002:1896"))
    # The gain base now carries a full bundle.
    assert archive.has(se_rk_current_json_locator("2015:284"))
    assert archive.has(se_current_ir_locator("2015:284"))


def test_scaled_ingest_se_sfst_oracles_is_idempotent_on_rerun() -> None:
    archive = _GlobArchive()
    _seed_se_amending_act(archive, "2018:111", "2015:284")
    archive.stored[se_rk_current_url("2015:284")] = _SE_SFST_REAL_PAGE.encode("utf-8")

    first = scaled_ingest_se_sfst_oracles(archive)
    assert first["added_count"] == 1
    first_blob = archive.stored[se_rk_current_json_locator("2015:284")]

    # A second default run re-enumerates the gain bases: the now-oracled base is
    # excluded from the gain set entirely, so nothing is even considered.
    second = scaled_ingest_se_sfst_oracles(archive)
    assert second["added_count"] == 0
    assert second["considered"] == 0
    # Even if the base is forced back into the gain set explicitly, the loop's
    # existing-oracle guard skips it without overwriting (protects real RK blobs).
    forced = scaled_ingest_se_sfst_oracles(archive, gain_bases=["2015:284"])
    assert forced["added_count"] == 0
    assert forced["skipped_existing_oracle_count"] == 1
    assert forced["considered"] == 1
    # The previously written oracle is byte-identical across both reruns.
    assert archive.stored[se_rk_current_json_locator("2015:284")] == first_blob


def test_aggregate_se_official_coverage_arithmetic_and_determinism() -> None:
    summaries = [
        {
            "amending_sfs_id": "2020:5",
            "outcome": "replay_ok",
            "target_count": 3,
            "match_count": 3,
            "genuine_content_match_count": 1,
            "editorial_match_count": 1,
            "official_oracle_match_count": 1,
            "classification_counts": {
                "exact": 1,
                "editorial_attribution_only": 1,
                "official_oracle_match_current_surface_drift": 1,
            },
        },
        {
            "amending_sfs_id": "2020:2",
            "outcome": "replay_ok",
            "target_count": 2,
            "match_count": 1,
            "genuine_content_match_count": 1,
            "editorial_match_count": 0,
            "official_oracle_match_count": 0,
            "classification_counts": {"exact": 1, "content_mismatch": 1},
        },
        {
            "amending_sfs_id": "2019:9",
            "outcome": "older_base_required",
            "error_type": "NotImplementedError",
        },
        {
            "amending_sfs_id": "2019:1",
            "outcome": "error",
            "error_type": "ValueError",
        },
    ]

    aggregate = aggregate_se_official_coverage(summaries)

    assert aggregate["works_scanned"] == 4
    assert aggregate["replay_ok_count"] == 2
    assert aggregate["older_base_required_count"] == 1
    assert aggregate["error_count"] == 1
    assert aggregate["section_target_count"] == 5
    assert aggregate["section_match_count"] == 4
    # 4/5 sections "match" but only 2 are genuine content matches — the honest
    # number must not be flattered by editorial/oracle-fallback buckets.
    assert aggregate["genuine_content_match_count"] == 2
    assert aggregate["editorial_only_match_count"] == 1
    assert aggregate["official_oracle_match_count"] == 1
    assert aggregate["section_match_rate"] == 0.8
    assert aggregate["genuine_content_match_rate"] == 0.4
    # Classification counts and error examples are emitted in sorted order.
    assert list(aggregate["classification_counts"]) == sorted(
        aggregate["classification_counts"]
    )
    assert aggregate["classification_counts"]["exact"] == 2
    assert list(aggregate["error_examples"]) == ["NotImplementedError", "ValueError"]
    # Pure aggregation: re-running yields the identical report.
    assert aggregate_se_official_coverage(summaries) == aggregate


def test_clean_se_pdf_text_drops_obvious_page_furniture() -> None:
    raw = (
        "SFS 2026:286\n\n1\n\n1 § Första raden.\nAndra raden.\n\nSida 2 av 3\n\n"
        "1234567890:;\n\nFörordning om riktålder för pension för år 20311\n\n"
        "1 Senaste förordning i ämnet 2024:299.\n\n2 § Andra paragrafen."
    )
    cleaned = clean_se_pdf_text(raw)

    assert "SFS 2026:286" not in cleaned
    assert "Sida 2 av 3" not in cleaned
    assert "1234567890:;" not in cleaned
    assert "\n1\n" not in cleaned
    assert "1 § Första raden." in cleaned
    assert "år 2031" in cleaned
    assert "2 § Andra paragrafen." in cleaned


def test_clean_se_pdf_text_preserves_standalone_sfs_statute_citation_reference_line() -> None:
    """Standalone SFS statute-citation reference lines MUST survive the cleanup.

    A Swedish SFS statute citation wrapped in parentheses ("(1992:394).") or as
    a bare bare statute-number line ("1985:1100.") is a legitimate cross-
    reference that appears on its own wrapped line in ``pdftotext`` output. Its
    shape (short: digits, colon, parens, period) matches the
    ``_DIGIT_GARBAGE_RE`` page-furniture filter (lines composed exclusively of
    digit/punctuation/whitespace, length >= 8), so the cleaner previously
    silently stripped them as page furniture. That truncated the surrounding
    provision's body when the citation wrapped onto its own line -- the suffix
    line was dropped, the section's last paragraph terminated at the wrap
    point, and downstream replay-vs-oracle comparison operated against a
    truncated replacement text. Real-corpus witness: SFS 2001:223 §2a
    replacement statement ends "...institut finnas i gymnasieförordningen\n
    (1992:394)." -- the "(1992:394)." line was silently stripped.

    Exempt the ``\\(?\\d{4}:\\d+\\)?\\.?`` shape from the garbage filter so the
    citation reference survives the cleanup and stays in the section body. The
    existing page-furniture lines (``1234567890:;``, ``Sida 2 av 3``) keep being
    dropped because they do not look like SFS citations.
    """
    raw_with_paren_citation = (
        "1 § Första stycket hänvisar här.\n"
        "(1992:394).\n"
        "2 § Andra paragrafens lydelse."
    )
    cleaned = clean_se_pdf_text(raw_with_paren_citation)
    # The wrapped citation reference survived -- it stays part of §1's body.
    assert "(1992:394)." in cleaned
    # Either it sits on its own line (carried through as a separate paragraph,
    # or the parser will fold it); either way it is NOT silently dropped.
    # And §2 still comes through intact -- the cleanup did not eat the
    # surrounding provision structure.
    assert "1 § Första stycket hänvisar här." in cleaned
    assert "2 § Andra paragrafens lydelse." in cleaned

    # Bare citation reference without parens, with trailing period:
    raw_bare_citation = "Det finns också en hänvisning.\n1985:1100.\nAvslutande rad."
    cleaned_bare = clean_se_pdf_text(raw_bare_citation)
    assert "1985:1100." in cleaned_bare

    # Garbage-qualifying lines that are NOT statute citations stay filtered:
    # the cleanup did not regress on its page-furniture scrub.
    assert "1234567890:;" not in clean_se_pdf_text("Första rad.\n1234567890:;\nAndra rad.")
    # The "Sida 2 av 3" page furniture line is still filtered:
    assert "Sida 2 av 3" not in clean_se_pdf_text("Rad ett.\nSida 2 av 3\nRad två.")


def test_parse_rk_issue_date_and_guess_pdf_url() -> None:
    html = '<span class="bold">Utfärdad:</span> 2025-05-22'
    issue_date = parse_se_rk_issue_date(html)

    assert issue_date == "2025-05-22"
    assert guess_se_official_pdf_url("2025:399", issue_date) == (
        "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf"
    )


def test_parse_se_official_act_text_extracts_amendment_surface() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2026:106) om\n"
        "kriminalvårdens behandling av personuppgifter inom\n"
        "brottsdatalagens område\n\n"
        "Publicerad\n"
        "den 24 mars 2026\n\n"
        "Utfärdad den 19 mars 2026\n"
        "Regeringen föreskriver att 2, 8 och 11 §§ förordningen (2026:106) om\n"
        "kriminalvårdens behandling av personuppgifter inom brottsdatalagens\n"
        "område ska ha följande lydelse.\n"
        "2 § Uppgifter om målsägande får göras gemensamt tillgängliga.\n"
        "8 § Uppgifter i säkerhetsregistret får lämnas ut.\n"
        "11 § Nedan angivna myndigheter ska lämna uppgifter till Kriminalvården.\n\n"
        "Denna förordning träder i kraft den 15 april 2026.\n"
        "På regeringens vägnar\n"
        "GUNNAR STRÖMMER\n"
        "Emelie Smiding\n"
        "(Justitiedepartementet)\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:286")

    assert act.sfs_id == "2026:286"
    assert act.title == (
        "Förordning om ändring i förordningen (2026:106) om kriminalvårdens behandling "
        "av personuppgifter inom brottsdatalagens område"
    )
    assert act.act_type == "förordning"
    assert act.is_amending_act is True
    assert act.amended_act_sfs_id == "2026:106"
    assert act.published_date == "2026-03-24"
    assert act.issued_date == "2026-03-19"
    assert act.affected_section_labels == ("2", "8", "11")
    assert [p.label for p in act.provisions] == ["2", "8", "11"]
    assert act.effective_clause == "Denna förordning träder i kraft den 15 april 2026."
    assert act.signatories == ("GUNNAR STRÖMMER", "Emelie Smiding", "(Justitiedepartementet)")


def test_parse_se_official_act_text_extracts_publication_date_from_legacy_utkom_fran_trycket_header() -> None:
    """Older SFS PDFs publish the date as a follow-up line under "Utkom från trycket".

    The newer PDF layout uses a standalone ``Publicerad`` header followed by a date
    line. Pre-2003 acts ship the older ``Utkom från trycket`` / ``den DD month YYYY``
    two-line block instead; the parser previously did not recognize that header as a
    publication-date block and folded both lines into the act title, leaving
    ``published_date`` empty. Downstream, the analyze path then raised
    ``ValueError("could not determine effective date for ...")`` for every older
    act that lacked an explicit entry-into-force clause and had no amendment
    register entry — covering a substantial slice of the 1999-2000 corpus.

    Regression: the parser MUST recognize the legacy header, MUST extract
    ``published_date`` from its follow-up line, and MUST NOT carry the header or
    date into the act title.
    """
    text = (
        "Svensk författningssamling\n"
        "SFS 1999:1062\n\n"
        "Lag\n"
        "om ändring i lagen (1999:353) om rättspsykiatriskt\n"
        "forskningsregister;\n\n"
        "Utkom från trycket\n"
        "den 13 december 1999\n\n"
        "utfärdad den 2 december 1999.\n"
        "Enligt riksdagens beslut1 föreskrivs att 5 § lagen (1999:353) om "
        "rättspsykiatriskt forskningsregister skall ha följande lydelse.\n"
        "5 § För varje person får uppgifter registreras.\n"
        "På regeringens vägnar\n"
        "LARS ENGQVIST\n"
        "(Socialdepartementet)\n"
    )

    act = parse_se_official_act_text(text, sfs_id="1999:1062")

    assert act.published_date == "1999-12-13"
    # Header and date string must NOT leak into the title (they previously did,
    # which masked the empty published_date field and prevented the analyze path
    # from using it).
    assert "Utkom" not in act.title
    assert "13 december 1999" not in act.title
    assert "trycket" not in act.title.lower()
    # The title otherwise carries the original human-readable heading intact,
    # modulo the traling semicolon stripping the parser already performs.
    assert "rättspsykiatriskt forskningsregister" in act.title
    # Issued-date handling unaffected: the legacy header occupies the same
    # position as ``Publicerad`` does in the newer layout.
    assert act.issued_date == "1999-12-02"


def test_parse_se_official_act_text_folds_wrapped_cross_reference_continuation_into_current_section() -> None:
    """Wrapped ``'<N> § första stycket och <M> §.'`` is a cross-reference, not a new section.

    Real-corpus witness: SFS 2001:606 — Lag om ändring i förordningen (2000:308)
    om fastighetsregister — amends sections 64, 72, 74. The officer's text of
    section 72 wraps onto a new PDF text line that begins with ``64 § första
    stycket och 67 §.`` (a cross-reference to two other sections inside §72's
    prose, not a new §64). Without this guard the parser emits a duplicate
    provision under label ``64`` — an unfalsified-looking fragment that
    displaces the legitimate ``64`` replacement text in the per-act
    ``official_provisions`` dict the replay-vs-oracle check consults. That made
    a correct replay-vs-later-consolidation disagreement look like a LawVM-side
    content_mismatch, since the lookup returned the wrong text.

    Synthetic test (mirrors the original witness shape without depending on the
    archived corpus): the parser MUST produce exactly one provision per
    affected label, the cross-reference line MUST fold into the current
    section's text, and the lookup for ``64`` MUST return the legitimate
    replacement text — never the continuation fragment.
    """
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2000:308) om fastighetsregister\n\n"
        "Publicerad\n"
        "den 1 september 2001\n\n"
        "Utfärdad den 16 augusti 2001\n"
        "Regeringen föreskriver att 64, 72 och 74 §§ förordningen (2000:308) "
        "om fastighetsregister skall ha följande lydelse.\n"
        "64 § I taxeringsuppgiftsdelen skall redovisas uppgifter från "
        "beskattningsdatabasen enligt lagen (2001:181).\n"
        "72 § Sedan en underrättelse som avses i 74 § har kommit in, skall "
        "Lantmäteriverket snarast möjligt i fastighetsregistret föra in de "
        "uppgifter som avses i\n"
        # The wrapped cross-reference continuation that previously masqueraded
        # as a new section start.
        "64 § första stycket och 67 §. Införingen av uppgifter från "
        "beskattningsdatabasen skall ske senast i samband med årsskifte.\n"
        "74 § Skattemyndigheten skall på upptagning för automatiserad behandling "
        "underrätta Lantmäteriverket.\n"
        "På regeringens vägnar\n"
        "LARS ENGQVIST\n"
        "(Finansdepartementet)\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2001:606")

    # Exactly one provision per affected label — the duplicate ``64`` provision
    # MUST NOT exist.
    labels = [p.label for p in act.provisions]
    assert labels == ["64", "72", "74"], labels

    # The wrapped cross-reference folded into §72's text, not §64's. The §64
    # oracle text is the legitimate replacement, not the continuation fragment.
    provisions_by_label = {p.label: p.text for p in act.provisions}
    assert provisions_by_label["64"].startswith("I taxeringsuppgiftsdelen")
    assert "första stycket och 67 §" in provisions_by_label["72"]
    # And the fragment did not silently leak into §64 — the principal replay
    # oracle lookup returns the legitimate replace text.
    assert "Införingen av uppgifter från beskattningsdatabasen" not in provisions_by_label["64"]


def test_coerce_se_official_act_folds_legacy_duplicate_label_provisions_into_host_section() -> None:
    """Runtime coercion repairs legacy cached duplicate-label payloads.

    Archaeic cached ``official.act.json`` rows (persisted before the parser fix
    in :func:`parse_se_official_act_text` learned to fold wrapped cross-reference
    continuations back into their host section) carry a duplicate provision whose
    text begins with ``<ordinal> stycket`` — the row the live parser no longer
    emits. The replay-vs-oracle lookup at the higher-level
    :func:`check_se_official_replay` consults the cached ``official_act`` raw
    dict, so without runtime coercion the duplicate label silently displaces the
    legitimate oracle text and a correct replay-vs-later-consolidation
    disagreement is misclassified as a LawVM-side ``content_mismatch``.

    Real-corpus witnesses:
      * SFS 2001:606 §64 — ``64 § första stycket och 67 §.`` line displaced the
        legitimate §64 replacement text.
      * SFS 2002:66 §14 — same wrapping idiom inside §1 of the same act.

    Regression: the runtime coercion MUST fold the duplicate-label cross-reference
    continuation into the prior provision's text and MUST NOT surface a duplicate
    label in the coerced ``SEOfficialActText.provisions`` tuple. A second
    duplicate label whose continuation shape is NOT recognised (e.g. two
    non-cross-reference occurrences) stays visible as a typed
    ``se_official_act_payload_row_duplicate_label`` diagnostic (no silent drop).
    """
    from lawvm.sweden.grafter import _coerce_official_act

    legacy_payload = {
        "sfs_id": "2001:606",
        "title": "Förordning om ändring i förordningen (2000:308) om fastighetsregister",
        "act_type": "förordning",
        "amended_act_sfs_id": "2000:308",
        "is_amending_act": True,
        "published_date": "2001-09-01",
        "issued_date": "2001-08-16",
        "enacting_clause": (
            "Regeringen föreskriver att 64, 72 och 74 §§ förordningen (2000:308) "
            "om fastighetsregister skall ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 oktober 2001.",
        "affected_section_labels": ["64", "72", "74"],
        "provisions": [
            {
                "label": "64",
                "text": (
                    "I taxeringsuppgiftsdelen skall redovisas uppgifter från "
                    "beskattningsdatabasen enligt lagen (2001:181)."
                ),
            },
            {
                "label": "72",
                "text": (
                    "Sedan en underrättelse som avses i 74 § har kommit in, skall "
                    "Lantmäteriverket föra in de uppgifter som avses i"
                ),
            },
            # Cached legacy duplicate-label payload — the wrapped cross-reference
            # continuation that the live parser no longer emits.
            {
                "label": "64",
                "text": (
                    "första stycket och 67 §. Införingen av uppgifter från "
                    "beskattningsdatabasen skall ske senast i samband med årsskifte."
                ),
            },
            {"label": "74", "text": "Skattemyndigheten skall underrätta Lantmäteriverket."},
        ],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    diagnostics: list[dict] = []
    coerced = _coerce_official_act(legacy_payload, diagnostics_out=diagnostics)

    # No duplicate-label entry — only one provision per affected label.
    labels = [p.label for p in coerced.provisions]
    assert labels == ["64", "72", "74"], labels

    # The cross-reference continuation folded into the prior provision's text
    # (the host section, not the legitimate §64 replacement).
    provisions_by_label = {p.label: p.text for p in coerced.provisions}
    assert "första stycket och 67 §" in provisions_by_label["72"]
    assert "Införingen av uppgifter från beskattningsdatabasen" in provisions_by_label["72"]
    # And the legitimate §64 replacement text is NOT displaced.
    assert provisions_by_label["64"].startswith("I taxeringsuppgiftsdelen")
    assert "Införingen av uppgifter från beskattningsdatabasen" not in provisions_by_label["64"]
    # The folding rule fired silently as a benign repair — no diagnostic emitted
    # (a cached legacy payload reconstruction, not an active parse failure).
    duplicate_rule_diagnostics = [
        d for d in diagnostics
        if d.get("rule_id") == "se_official_act_payload_row_duplicate_label"
    ]
    assert duplicate_rule_diagnostics == [], (
        "cross-reference-continuation fold should NOT raise a duplicate-label "
        "diagnostic — that diagnostic is reserved for genuinely ambiguous "
        f"duplicate-label rows. Got: {duplicate_rule_diagnostics}"
    )

    # And a non-cross-reference duplicate label stays visible as a typed
    # diagnostic — the fold is shape-specific, never a blanket dedupe.
    ambiguous_payload = dict(legacy_payload)
    ambiguous_payload["provisions"] = list(legacy_payload["provisions"]) + [
        {"label": "64", "text": "En orelaterad tredje lydelse under samma etikett."}
    ]
    ambiguous_diagnostics: list[dict] = []
    _coerce_se_official_act_ambiguous = _coerce_official_act(
        ambiguous_payload, diagnostics_out=ambiguous_diagnostics
    )
    assert any(
        d.get("rule_id") == "se_official_act_payload_row_duplicate_label"
        for d in ambiguous_diagnostics
    ), "non-cross-reference duplicate labels MUST surface a typed diagnostic"


def test_coerce_se_official_act_drops_companion_ghost_inserted_heading_when_provision_folds() -> None:
    """Plural-section citation wrap-continuation ghost heading companion drop.

    The pre-fix parser emitted two paired artifacts when a paragraph across lines
    wrapped so that the leading ``<N> §`` of one line crossed a ``<N> §§ <text>``
    plural-citation wrap:

    * a ghost provided with label ``N`` whose text is the wrapped citation tail
      ``§ socialtjänstlagen (1980:620) samt åtgärder enligt lagen (1990:52) med...
      `` (the live parser no longer emits this — folded by the parser-side
      ``_is_cross_reference_continuation`` guard); and
    * a ghost ``inserted_heading`` row whose ``before_label`` is the ghost
      label ``N`` and whose text is the preceding paragraph's final line that
      the parser mistook for a heading (e.g. ``umgänge med barn...``).

    Real-corpus witness: SFS 2001:416 §11 — the §11 list-of-authorities wraps
    across two PDF text lines ``...enligt 25–28, 30 och\n31 §§ socialtjänstlagen
    (1980:620)`` and the OLD parser cached it as ``provisions=[{label:'11'},
    {label:'31', text:'§ socialtjänstlagen...'}]`` and ``inserted_headings=
    [{before_label:'31', text:'umgänge med barn...'}]``. Without dropping the
    companion heading the runtime coercion leaves the inserted_heading intact,
    the lowering emits a §31 INSERT op with empty payload, replay applies a
    ghost modification, and check_se_official_replay reports a §31
    ``content_mismatch`` row that is not real.

    Regression: the runtime coercion MUST fold the ghost provision's text into
    the prior provision AND MUST drop the companion inserted_heading silently
    (no diagnostic — the fold is benign cached-act reconciliation). The coerced
    act carries one provision per affected label and zero ghost headings.
    """
    from lawvm.sweden.grafter import _coerce_official_act

    legacy_payload = {
        "sfs_id": "2001:416",
        "title": "Förordning om ändring i förordningen (1999:1134) om belastningsregister",
        "act_type": "förordning",
        "amended_act_sfs_id": "1999:1134",
        "is_amending_act": True,
        "published_date": "2001-06-07",
        "issued_date": "2001-05-25",
        "enacting_clause": (
            "Regeringen föreskriver att 11 § förordningen (1999:1134) om "
            "belastningsregister skall ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 juli 2001.",
        "affected_section_labels": ["11"],
        # Real §11 replacement text — truncated by the OLD parser at the wrap point.
        "provisions": [
            {
                "label": "11",
                "text": "Uppgifter ur belastningsregistret skall lämnas ut om det begärs av 1. Justitiekanslern.",
            },
            # Ghost §11-provision-tail row: the wrapped cross-reference
            # continuation the OLD parser emitted under the label '31'.
            {
                "label": "31",
                "text": (
                    "§ socialtjänstlagen (1980:620) samt åtgärder enligt lagen "
                    "(1990:52) med särskilda bestämmelser om vård av unga."
                ),
            },
        ],
        # Companion ghost inserted_heading: the OLD parser mistook the line
        # just before the false `§ marker` line for a heading and labeled it
        # with the ghost label as before_label.
        "inserted_headings": [
            {"before_label": "31", "text": "umgänge med barn, medgivande att ta emot barn m.m. enligt 25–28, 30 och"}
        ],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    diagnostics: list[dict] = []
    coerced = _coerce_official_act(legacy_payload, diagnostics_out=diagnostics)

    # Provision labels — only the legitimate §11 survives; the ghost §31
    # folded back into its host section.
    assert [p.label for p in coerced.provisions] == ["11"]
    provisions_by_label = {p.label: p.text for p in coerced.provisions}
    assert "§ socialtjänstlagen (1980:620)" in provisions_by_label["11"]
    # And the companion inserted_heading MUST be dropped silently — the
    # before_label matching the folded ghost label signals the heading was
    # the OLD-parser artifact, not an independent legit heading.
    assert coerced.inserted_headings == (), coerced.inserted_headings
    # No diagnostic — the fold is benign cached-act reconciliation, not a
    # schema drift worth surfacing.
    assert diagnostics == [], diagnostics
    # And the inserted_heading DROP is fold-companion-specific: an unclaimed
    # heading whose before_label is NOT a folded ghost is preserved (so the
    # effect-plan can still surface it as an unclaimed-payload adjudication).
    legacy_payload_with_unclaimed_heading = dict(legacy_payload)
    legacy_payload_with_unclaimed_heading["inserted_headings"] = [
        {"before_label": "9", "text": "Rubrik utan stöd i klausul"}
    ]
    coerced_with_unclaimed_heading = _coerce_official_act(legacy_payload_with_unclaimed_heading)
    assert coerced_with_unclaimed_heading.inserted_headings != (), (
        "the unclaimed-heading case (before_label NOT a folded ghost label) "
        "MUST be preserved so the effect-plan can surface unclaimed payloads"
    )


def test_compile_se_official_ops_recover_base_act_id_from_enacting_clause() -> None:
    cases = [
        (
            "2002:1150",
            "1999:175",
            "Förordning om ändring i rättsinformationsförordningen Utkom från trycket den 30 december 2002",
            "Regeringen föreskriver att 15 § rättsinformationsförordningen (1999:175) skall ha följande lydelse.",
            [{"label": "15", "text": "Ny lydelse."}],
        ),
        (
            "2002:1174",
            "1997:383",
            "Förordning om ändring i växtförädlarrättsförordningen Utkom från trycket den 16 januari 2003",
            "Regeringen föreskriver att 2 § växtförädlarrättsförordningen (1997:383) skall ha följande lydelse.",
            [{"label": "2", "text": "Ny lydelse."}],
        ),
    ]

    for sfs_id, base_sfs_id, title, enacting_clause, provisions in cases:
        act = {
            "sfs_id": sfs_id,
            "title": title,
            "act_type": "förordning",
            "amended_act_sfs_id": "",
            "is_amending_act": True,
            "published_date": "2003-01-01",
            "issued_date": "2002-12-20",
            "enacting_clause": enacting_clause,
            "effective_clause": "Denna förordning träder i kraft den 1 januari 2003.",
            "affected_section_labels": [provisions[0]["label"]],
            "provisions": provisions,
            "signatories": [],
            "footnotes": [],
        }
        archive = _FakeArchive(
            stored={
                f"se://sfs/{sfs_id}/official.act.json": json.dumps(act, ensure_ascii=False).encode("utf-8"),
            }
        )

        ops = compile_se_official_ops_to_archive(archive, sfs_id)
        clause_surface = load_se_official_clause_surface_from_archive(archive, sfs_id)
        effects_plan = load_se_official_effects_plan_from_archive(archive, sfs_id)

        assert clause_surface is not None
        assert clause_surface["amended_act_sfs_id"] == base_sfs_id
        assert effects_plan is not None
        assert effects_plan["frontier_classification"] == "supported"
        assert len(ops) == 1
        assert ops[0]["action"] == "replace"
        assert f"base_sfs_id={base_sfs_id}" in ops[0]["provenance_tags"]
        assert se_official_ops_locator(sfs_id) in archive.stored


def test_build_se_official_base_statute_builds_flat_seed_ir() -> None:
    act = {
        "sfs_id": "2015:284",
        "title": "Förordning (2015:284) med instruktion för Socialstyrelsen",
        "act_type": "förordning",
        "is_amending_act": False,
        "published_date": "2015-05-26",
        "issued_date": "2015-05-21",
        "provisions": [
            {"label": "1", "text": "Socialstyrelsen är förvaltningsmyndighet.\n\n1. Första punkten."},
            {"label": "2", "text": "Myndigheten ska också göra annat."},
        ],
        "inserted_headings": [
            {"before_label": "1", "text": "Uppgifter"},
        ],
        "appendices": [
            {"label": "1", "title": "Bilagerubrik", "text": "1. A\n\n2. B"},
        ],
    }

    statute = build_se_official_base_statute(act)

    assert statute.statute_id == "2015:284"
    assert statute.metadata["source_confidence"] == "OFFICIAL_PDF_ONLY"
    assert [child.kind for child in statute.body.children] == [IRNodeKind.HEADING, IRNodeKind.SECTION, IRNodeKind.SECTION]
    assert statute.body.children[0].text == "Uppgifter"
    assert statute.body.children[1].label == "1"
    assert statute.body.children[1].children[0].text == "Socialstyrelsen är förvaltningsmyndighet."
    assert statute.body.children[1].children[0].children[0].label == "1"
    assert statute.supplements[0].label == "1"


def test_parse_se_official_act_text_keeps_wrapped_cross_reference_inside_same_section() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2026:106) om något\n\n"
        "Publicerad\n"
        "den 24 mars 2026\n\n"
        "Utfärdad den 19 mars 2026\n"
        "Regeringen föreskriver att 2 § förordningen (2026:106) om något ska ha följande lydelse.\n"
        "2 § Uppgifter enligt 27 §, 35 § eller 3 kap.\n"
        "2 § förordningen (2025:1323) får behandlas.\n"
        "Denna förordning träder i kraft den 15 april 2026.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:286")

    assert [p.label for p in act.provisions] == ["2"]
    assert "2 § förordningen (2025:1323)" in act.provisions[0].text


def test_parse_se_official_act_text_keeps_effective_clause_after_trailing_footnote_line() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2015:284) med instruktion för Socialstyrelsen\n\n"
        "Publicerad\n"
        "den 24 mars 2026\n\n"
        "Utfärdad den 19 mars 2026\n"
        "Regeringen föreskriver att 18 § förordningen (2015:284) med instruktion för Socialstyrelsen ska ha följande lydelse.\n"
        "18 §1 Inom myndigheten finns ett särskilt beslutsorgan.\n"
        "Denna förordning träder i kraft den 15 april 2026.\n"
        "1 Senaste lydelse 2025:1426.\n"
        "På regeringens vägnar\n"
        "ELISABET LANN\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:280")

    assert act.effective_clause == "Denna förordning träder i kraft den 15 april 2026."
    assert act.provisions[0].text.startswith("Inom myndigheten finns ett särskilt beslutsorgan.")
    assert act.footnotes == ("1 Senaste lydelse 2025:1426.",)


def test_parse_se_official_act_text_keeps_effective_clause_after_embedded_footnote_line() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet\n\n"
        "Publicerad\n"
        "den 28 mars 2026\n\n"
        "Utfärdad den 26 mars 2026\n"
        "Regeringen föreskriver1 i fråga om förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet dels att 2 § ska ha följande lydelse.\n"
        "2 § Denna förordning är meddelad med stöd av 6 § lagen (2023:254).\n"
        "7 a § De kriterier för funktionsprestanda som anges i bilaga 3 ska användas.\n"
        "1 Jfr Europaparlamentets och rådets direktiv (EU) 2019/882.\n"
        "Denna förordning träder i kraft den 1 maj 2026.\n"
        "På regeringens vägnar\n"
        "CAMILLA WALTERSSON GRÖNVALL\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:290")

    assert act.effective_clause == "Denna förordning träder i kraft den 1 maj 2026."
    assert act.footnotes == ("1 Jfr Europaparlamentets och rådets direktiv (EU) 2019/882.",)


def test_parse_se_official_act_text_drops_blank_line_footnote_continuation_from_section_text() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet\n\n"
        "Publicerad\n"
        "den 28 mars 2026\n\n"
        "Utfärdad den 26 mars 2026\n"
        "Regeringen föreskriver att 7 a § förordningen (2023:676) ska ha följande lydelse.\n"
        "7 a § Text före störning resul-\n\n"
        "1 Jfr Europaparlamentets och rådets direktiv (EU) 2019/882 av den 17 april 2019 om tillgänglig-\n\n"
        "hetskrav för produkter och tjänster, i den ursprungliga lydelsen.\n\n"
        "terar i likvärdig eller ökad tillgänglighet.\n"
        "Denna förordning träder i kraft den 1 maj 2026.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:290")

    assert "hetskrav för produkter" not in act.provisions[0].text
    assert "resul-\n\nterar i likvärdig" in act.provisions[0].text


def test_parse_se_official_act_text_extracts_heading_and_appendix_for_mixed_act() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet\n\n"
        "Publicerad\n"
        "den 28 mars 2026\n\n"
        "Utfärdad den 26 mars 2026\n"
        "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet "
        "dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, en ny bilaga, bilaga 3, "
        "och närmast före 7 a § en ny rubrik av följande lydelse.\n"
        "2 § Denna förordning är meddelad med stöd av 6 § lagen (2023:254).\n\n"
        "Kriterier för funktionsprestanda\n\n"
        "7 a § De kriterier för funktionsprestanda som anges i bilaga 3 till denna förordning ska användas.\n"
        "Denna förordning träder i kraft den 1 maj 2026.\n"
        "På regeringens vägnar\n"
        "CAMILLA WALTERSSON GRÖNVALL\n"
        "Bilaga 3\n"
        "Kriterier för funktionsprestanda\n\n"
        "1. Om produkten har visuella driftslägen ska den ha minst ett driftsläge som inte kräver syn.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:290")

    assert [p.label for p in act.provisions] == ["2", "7a"]
    assert act.provisions[0].text == "Denna förordning är meddelad med stöd av 6 § lagen (2023:254)."
    assert act.inserted_headings[0].before_label == "7a"
    assert act.inserted_headings[0].text == "Kriterier för funktionsprestanda"
    assert act.appendices[0].label == "3"
    assert act.appendices[0].title == "Kriterier för funktionsprestanda"
    assert "1. Om produkten har visuella driftslägen" in act.appendices[0].text


def test_parse_and_compile_se_official_act_text_recovers_unlabeled_appendix_payload() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet\n\n"
        "Publicerad\n"
        "den 28 mars 2026\n\n"
        "Utfärdad den 26 mars 2026\n"
        "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet "
        "dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, en ny bilaga, "
        "bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse.\n"
        "2 § Denna förordning är meddelad med stöd av 6 § lagen (2023:254).\n\n"
        "Kriterier för funktionsprestanda\n\n"
        "7 a § De kriterier för funktionsprestanda som anges i bilaga 3 till denna förordning ska användas.\n"
        "Denna förordning träder i kraft den 1 maj 2026.\n"
        "På regeringens vägnar\n"
        "CAMILLA WALTERSSON GRÖNVALL\n"
        "Bilaga\n"
        "Kriterier för funktionsprestanda\n\n"
        "1. Om produkten har visuella driftslägen ska den ha minst ett driftsläge som inte kräver syn.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2026:290")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2026:290")

    assert act.appendices[0].label == ""
    assert act.appendices[0].title == "Kriterier för funktionsprestanda"
    assert plan.frontier_classification == "supported"
    assert plan.planned_operation_count == 4
    assert [item.kind for item in plan.planned_items] == [
        "replace_section",
        "insert_section",
        "insert_heading",
        "insert_appendix",
    ]
    assert [item.target_label for item in plan.planned_items] == ["2", "7a", "7a", "3"]
    assert len(ops) == 4
    assert ops[-1].action is StructuralAction.INSERT
    assert ops[-1].target.leaf_label() == "3"
    assert ops[-1].payload is not None and ops[-1].payload.kind is IRNodeKind.APPENDIX


def test_parse_and_compile_se_official_act_text_recovers_embedded_labeled_appendix_payload() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (1991:1427) om tjänstepension och tjänstegrupplivförsäkring för vissa arbetstagare med icke-statlig anställning\n\n"
        "Publicerad\n"
        "den 5 februari 2002\n\n"
        "Utfärdad den 31 januari 2002\n"
        "Regeringen föreskriver att bilaga A till förordningen (1991:1427) om tjänstepension och tjänstegrupplivförsäkring "
        "för vissa arbetstagare med icke-statlig anställning skall ha följande lydelse.\n"
        "Bilaga A\n"
        "Förteckning över icke-statliga anställningar\n\n"
        "1. Exempelanställning\n"
        "Denna förordning träder i kraft den 1 mars 2002.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2002:10")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:10")

    assert [appendix.label for appendix in act.appendices] == ["A"]
    assert act.appendices[0].title == "Förteckning över icke-statliga anställningar"
    assert plan.frontier_classification == "supported"
    assert [item.kind for item in plan.planned_items] == ["insert_appendix"]
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.leaf_kind() == "appendix"
    assert ops[0].target.leaf_label() == "A"


def test_parse_and_compile_se_official_act_text_recovers_ocr_marked_appendix_payload() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i jordförvärvsförordningen (1991:736)\n\n"
        "Publicerad\n"
        "den 26 februari 2002\n\n"
        "Utfärdad den 21 februari 2002\n"
        "Regeringen föreskriver att bilagan (1991:736) skall ha följande lydelse.\n"
        "till\n\n"
        "jordförvärvsförordningen\n\n"
        "Bilaga*1 Följande kommuner eller delar av kommuner är omarronderingsområden\n\n"
        "Dalarnas län\n\n"
        "I Säters kommun Säters, Stora Skedvi och Gustafs församlingar\n"
        "Denna förordning träder i kraft den 1 april 2002.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2002:60")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:60")

    assert [appendix.label for appendix in act.appendices] == ["1"]
    assert act.appendices[0].title == "Följande kommuner eller delar av kommuner är omarronderingsområden"
    assert plan.frontier_classification == "supported"
    assert [item.kind for item in plan.planned_items] == ["insert_appendix"]
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.leaf_kind() == "appendix"
    assert ops[0].target.leaf_label() == "1"


def test_parse_se_official_act_text_classifies_label_less_appendix_frontier_detail() -> None:
    text = (
        "Svensk författningssamling\n"
        "Lag\n"
        "om ändring i lagen (1988:950) om kulturminnen m.m.\n\n"
        "Publicerad\n"
        "den 20 december 2002\n\n"
        "Utfärdad den 19 december 2002\n"
        "Enligt riksdagens beslut föreskrivs att bilagan till lagen (1988:950) om kulturminnen m.m. skall ha följande lydelse.\n"
        "Denna lag träder i kraft den 1 januari 2003.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2002:1090")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))

    assert act.appendices == ()
    assert plan.frontier_classification == "empty_effect_plan_with_clause_targets"
    assert plan.frontier_detail == "appendix_clause_only_unlabeled"


def test_parse_se_official_act_text_classifies_point_clause_before_bilaga_reference() -> None:
    text = (
        "Svensk författningssamling\n"
        "Lag\n"
        "om ändring i lagen (1996:1512) om dubbelbeskattningsavtal mellan de nordiska länderna\n\n"
        "Publicerad\n"
        "den 13 december 2002\n\n"
        "Utfärdad den 5 december 2002\n"
        "Enligt riksdagens beslut föreskrivs att punkt 4 i övergångsbestämmelserna till lagen (1996:1512) om "
        "dubbelbeskattningsavtal mellan de nordiska länderna skall ha följande lydelse. 4.2 Bestämmelsen i artikel "
        "31 punkt 3 i avtalet skall också tillämpas vid 2002– 2004 års taxeringar. Vid 1999– 2004 års taxeringar "
        "skall artikel 26 punkt 2 i avtalet inte gälla i fall då artikel 31 punkt 3 i avtalet tillämpas. Vad som "
        "föreskrivs i första stycket skall, oberoende av bestämmelserna i bilaga 2 till denna lag, också gälla för "
        "inkomst som person med hemvist i Sverige uppbär för arbete ombord på danskt skepp i internationell "
        "färjetrafik mellan Sverige och Danmark om – skeppet är registrerat i det danska internationella "
        "skeppsregistret (DIS), – arbetsgivaren har hemvist i Danmark, och – personen var anställd ombord på ett "
        "danskt skepp någon gång under januari månad år 2001.\n"
        "Denna lag träder i kraft den 1 januari 2003.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2002:964")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))

    assert plan.frontier_classification == "empty_effect_plan_with_clause_targets"
    assert plan.frontier_detail == "point_clause_only"


def test_parse_se_official_act_text_extracts_heading_before_first_inserted_section() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning\n"
        "om ändring i förordningen (2015:284) med instruktion för Socialstyrelsen;\n\n"
        "Utkom från trycket\n"
        "den 30 januari 2018\n\n"
        "utfärdad den 18 januari 2018.\n"
        "Regeringen föreskriver i fråga om förordningen (2015:284) med instruktion för Socialstyrelsen\n"
        "dels att 16 och 22 §§ ska upphöra att gälla,\n"
        "dels att det ska införas en ny paragraf, 7 a §, och närmast före 7 a § en ny\n"
        "rubrik av följande lydelse.\n\n"
        "Uppgifter som rör donations- och transplantationsfrågor\n"
        "7 a § Socialstyrelsen ska ansvara för nationell samordning.\n"
        "Denna förordning träder i kraft den 1 mars 2018.\n"
    )

    act = parse_se_official_act_text(text, sfs_id="2018:11")

    assert act.affected_section_labels == ("16", "22")
    assert [heading.before_label for heading in act.inserted_headings] == ["7a"]
    assert [heading.text for heading in act.inserted_headings] == [
        "Uppgifter som rör donations- och transplantationsfrågor"
    ]
    assert [provision.label for provision in act.provisions] == ["7a"]


def test_parse_se_official_act_text_strips_senaste_lydelse_footnote_block() -> None:
    text = (
        "Svensk författningssamling\n"
        "Förordning om ändring i förordningen (2015:284) om något\n\n"
        "Publicerad\n"
        "den 10 februari 2026\n\n"
        "Utfärdad den 5 februari 2026\n"
        "Regeringen föreskriver i fråga om förordningen (2015:284) om något1\n"
        "dels att 16, 17 och 22 §§ ska upphöra att gälla,\n"
        "dels att nuvarande 17 a, 17 b och 22 a §§ ska betecknas 16, 17 och 22 §§,\n"
        "dels att 3, 4, 11 och 21 §§ ska ha följande lydelse,\n"
        "dels att det ska införas två nya paragrafer, 5 a och 15 b §§, av följande lydelse.\n"
        "4 §2 Socialstyrelsen ska ... 8. följa, analysera och rapportera ... genom\n"
        "statistikframställning, uppföljning, utvärdering och epidemiologiska studier,\n\n"
        "1 Senaste lydelse av\n\n"
        "16 § 2018:1381\n\n"
        "17 § 2020:133\n\n"
        "17 a § 2025:661\n"
        "2 Senaste lydelse 2025:496.\n\n"
        "17 b § 2024:1311\n\n"
        "22 § 2018:1381\n\n"
        "22 a § 2021:201.\n\n"
        "9. följa forsknings- och utvecklingsarbete av särskild betydelse ...\n\n"
        "5a § Ny 5 a.\n\n"
        "11 §3 Ny 11.\n\n"
        "15b § Ny 15 b.\n\n"
        "21 § Ny 21.\n\n"
        "Denna förordning träder i kraft den 1 mars 2026.\n"
    )

    act = parse_se_official_act_text(text, "2026:63")

    assert act.effective_clause == "Denna förordning träder i kraft den 1 mars 2026."
    assert [provision.label for provision in act.provisions] == ["4", "5a", "11", "15b", "21"]
    assert act.provisions[0].text.startswith("Socialstyrelsen ska")
    assert "2020:133" not in act.provisions[0].text
    assert "9. följa forsknings- och utvecklingsarbete" in act.provisions[0].text
    assert act.provisions[2].text == "Ny 11."
    assert not act.inserted_headings


def test_fetch_official_archives_parsed_official_act_json(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2025399.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2025-05/SFS2025-399.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    monkeypatch.setattr(
        "lawvm.sweden.fetch.se_pdf_bytes_to_text",
        lambda pdf_bytes, findings_out=None: (
            "Svensk författningssamling\nFörordning\nom riktålder för pension för år 2031\n\n"
            "Publicerad\nden 27 maj 2025\n\nUtfärdad den 22 maj 2025\n"
            "Regeringen föreskriver följande.\n"
            "1 § Riktåldern för pension ska vara 67 år för år 2031.\n"
            "Denna förordning träder i kraft den 1 juli 2025.\n"
            "På regeringens vägnar\nJAKOB FORSSMED\n"
        ),
    )

    fetch_se_official_artifacts("2025:399", archive)
    act = load_se_official_act_from_archive(archive, "2025:399")

    assert act is not None
    assert act["title"] == "Förordning om riktålder för pension för år 2031"
    assert act["provisions"][0]["label"] == "1"


def test_ingest_se_scraped_doc_html_map_archives_real_doc_urls_and_locators() -> None:
    archive = _FakeArchive()
    payload = cast(
        "dict[str, str]",
        {
        "https://svenskforfattningssamling.se/doc/2026286.html": (
            '<main><div class="field--item">2026:286</div>'
            '<a href="../sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a></main>'
        ),
        "https://example.com/not-sweden": "<main>ignore</main>",
        "https://svenskforfattningssamling.se/doc/2026399.html": b"<main>bytes are invalid here</main>",
        },
    )

    result = ingest_se_scraped_doc_html_map(payload, archive)

    assert result["entry_count"] == 3
    assert result["imported_count"] == 1
    assert result["skipped_count"] == 2
    assert [entry["rule_id"] for entry in result["skipped_entries"]] == [
        "se_scraped_doc_entry_unrecognized_url",
        "se_scraped_doc_entry_invalid_shape",
    ]
    assert all(entry["family"] == "source_pathology" for entry in result["skipped_entries"])
    assert all(entry["blocking"] is True for entry in result["skipped_entries"])
    assert all(entry["strict_disposition"] == "block" for entry in result["skipped_entries"])
    assert all(entry["quirks_disposition"] == "record" for entry in result["skipped_entries"])
    assert result["resolved_pdf_link_count"] == 1
    assert "https://svenskforfattningssamling.se/doc/2026286.html" in archive.stored
    assert se_official_doc_locator("2026:286") in archive.stored


def test_compile_se_official_act_ops_emits_replace_ops_for_simple_section_family() -> None:
    act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2, 8 och 11 §§ förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2", "8", "11"],
        "provisions": [
            {"label": "2", "text": "Uppgifter om målsägande får göras gemensamt tillgängliga."},
            {"label": "8", "text": "Uppgifter i säkerhetsregistret får lämnas ut."},
            {"label": "11", "text": "Nedan angivna myndigheter ska lämna uppgifter.\n\n1. Polismyndigheten"},
        ],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2026:286")

    assert [op.action.value for op in ops] == ["replace", "replace", "replace"]
    assert [op.target.path for op in ops] == [
        (("section", "2"),),
        (("section", "8"),),
        (("section", "11"),),
    ]
    assert ops[0].source is not None
    assert ops[0].source.statute_id == "2026:286"
    assert ops[0].source.effective == "2026-04-15"
    assert "base_sfs_id=2026:106" in ops[0].provenance_tags
    assert ops[2].payload is not None
    assert ops[2].payload.label == "11"


def test_compile_se_official_act_ops_records_non_amending_act_skip() -> None:
    act = {
        "sfs_id": "2026:106",
        "title": "Förordning (2026:106) om test",
        "act_type": "förordning",
        "amended_act_sfs_id": "",
        "is_amending_act": False,
        "published_date": "2026-01-01",
        "issued_date": "2025-12-20",
        "enacting_clause": "",
        "effective_clause": "Denna förordning träder i kraft den 1 januari 2026.",
        "affected_section_labels": [],
        "provisions": [
            {"label": "1", "text": "Denna förordning gäller test."},
        ],
        "signatories": [],
        "footnotes": [],
    }
    adjudications: list[CompileAdjudication] = []

    ops = compile_se_official_act_ops(act, source_id="2026:106", adjudications_out=adjudications)

    assert ops == []
    assert len(adjudications) == 1
    adjudication = adjudications[0]
    assert adjudication.kind == "se_official_non_amending_act_ops_skipped"
    assert adjudication.source_statute == "2026:106"
    assert adjudication.detail["rule_id"] == "se_official_non_amending_act_ops_skipped"
    assert adjudication.detail["phase"] == "lowering"
    assert adjudication.detail["blocking"] is False
    assert adjudication.detail["strict_disposition"] == "record"
    assert adjudication.detail["quirks_disposition"] == "record"
    assert adjudication.detail["frontier_classification"] == "non_amending"
    assert adjudication.detail["planned_operation_count"] == 1


def test_compile_se_official_act_ops_records_malformed_payload_rows() -> None:
    act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            "not an object",
            {"label": "", "text": "Unlabeled text"},
            {"label": "2", "text": "Giltig lydelse."},
        ],
        "inserted_headings": [
            "not an object",
            {"before_label": "", "text": "Rubrik utan mål"},
        ],
        "signatories": [],
        "footnotes": [],
    }
    adjudications: list[CompileAdjudication] = []

    ops = compile_se_official_act_ops(act, source_id="2026:286", adjudications_out=adjudications)

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "2"),)
    payload_row_adjudications = [
        adjudication
        for adjudication in adjudications
        if adjudication.kind == "se_official_act_payload_row_skipped"
    ]
    assert [adjudication.detail["rule_id"] for adjudication in payload_row_adjudications] == [
        "se_official_act_payload_row_invalid_shape",
        "se_official_act_payload_row_unlabeled",
        "se_official_act_payload_row_invalid_shape",
        "se_official_act_payload_row_unlabeled",
    ]
    assert [adjudication.detail["row_family"] for adjudication in payload_row_adjudications] == [
        "provisions",
        "provisions",
        "inserted_headings",
        "inserted_headings",
    ]
    assert all(adjudication.detail["phase"] == "payload" for adjudication in payload_row_adjudications)
    assert all(adjudication.detail["strict_disposition"] == "block" for adjudication in payload_row_adjudications)


def test_build_se_official_clause_surface_extracts_targets_without_changing_shape() -> None:
    act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2, 8 och 11 §§ förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2", "8", "11"],
        "provisions": [
            {"label": "2", "text": "Uppgifter om målsägande får göras gemensamt tillgängliga."},
            {"label": "8", "text": "Uppgifter i säkerhetsregistret får lämnas ut."},
            {"label": "11", "text": "Nedan angivna myndigheter ska lämna uppgifter.\n\n1. Polismyndigheten"},
        ],
        "signatories": [],
        "footnotes": [],
    }

    surface = _build_se_official_clause_surface(_coerce_official_act(act))

    assert surface.sfs_id == "2026:286"
    assert surface.replace_section_labels == ("2", "8", "11")
    assert surface.effective_date == "2026-04-15"


def test_build_se_official_payload_surface_groups_body_material_without_lowering() -> None:
    act = {
        "sfs_id": "2026:290",
        "title": "Förordning om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "act_type": "förordning",
        "amended_act_sfs_id": "2023:676",
        "is_amending_act": True,
        "published_date": "2026-03-28",
        "issued_date": "2026-03-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters "
            "tillgänglighet dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, "
            "en ny bilaga, bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 maj 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            {"label": "2", "text": "Denna förordning är meddelad med stöd av 6 § lagen (2023:254)."},
            {"label": "7a", "text": "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."},
        ],
        "inserted_headings": [
            {"before_label": "7a", "text": "Kriterier för funktionsprestanda"},
        ],
        "appendices": [
            {
                "label": "3",
                "title": "",
                "text": "1. Om produkten har visuella driftslägen ska den ha minst ett driftsläge som inte kräver syn.",
            },
        ],
        "signatories": [],
        "footnotes": [],
    }

    payload_surface = _build_se_official_payload_surface(_coerce_official_act(act))

    assert [provision.label for provision in payload_surface.provisions] == ["2", "7a"]
    assert [heading.before_label for heading in payload_surface.inserted_headings] == ["7a"]
    assert [appendix.label for appendix in payload_surface.appendices] == ["3"]


def test_build_se_official_elaboration_combines_clause_and_payload_waists() -> None:
    act = {
        "sfs_id": "2026:290",
        "title": "Förordning om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "act_type": "förordning",
        "amended_act_sfs_id": "2023:676",
        "is_amending_act": True,
        "published_date": "2026-03-28",
        "issued_date": "2026-03-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters "
            "tillgänglighet dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, "
            "en ny bilaga, bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 maj 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            {"label": "2", "text": "Denna förordning är meddelad med stöd av 6 § lagen (2023:254)."},
            {"label": "7a", "text": "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."},
        ],
        "inserted_headings": [
            {"before_label": "7a", "text": "Kriterier för funktionsprestanda"},
        ],
        "appendices": [
            {
                "label": "3",
                "title": "",
                "text": "1. Om produkten har visuella driftslägen ska den ha minst ett driftsläge som inte kräver syn.",
            },
        ],
        "signatories": [],
        "footnotes": [],
    }

    intent = _build_se_official_elaboration(_coerce_official_act(act))

    assert intent.issued_date == "2026-03-26"
    assert intent.clause_surface.replace_section_labels == ("2",)
    assert list(intent.provision_labels) == ["2", "7a"]
    assert list(intent.inserted_heading_labels) == ["7a"]
    assert list(intent.appendix_labels) == ["3"]


def test_compile_se_official_act_ops_emits_mixed_replace_insert_heading_and_appendix_ops() -> None:
    act = {
        "sfs_id": "2026:290",
        "title": "Förordning om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "act_type": "förordning",
        "amended_act_sfs_id": "2023:676",
        "is_amending_act": True,
        "published_date": "2026-03-28",
        "issued_date": "2026-03-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters "
            "tillgänglighet dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, "
            "en ny bilaga, bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 maj 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            {"label": "2", "text": "Denna förordning är meddelad med stöd av 6 § lagen (2023:254)."},
            {"label": "7a", "text": "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."},
        ],
        "inserted_headings": [
            {"before_label": "7a", "text": "Kriterier för funktionsprestanda"},
        ],
        "appendices": [
            {
                "label": "3",
                "title": "",
                "text": "1. Om produkten har visuella driftslägen ska den ha minst ett driftsläge som inte kräver syn.",
            },
        ],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2026:290")

    assert [op.action.value for op in ops] == ["replace", "insert", "insert", "insert"]
    assert [op.target.path for op in ops] == [
        (("section", "2"),),
        (("section", "7a"),),
        (("section", "7a"),),
        (("appendix", "3"),),
    ]
    assert ops[2].target.special == FacetKind.HEADING
    assert ops[3].payload is not None
    assert ops[3].payload.kind is IRNodeKind.APPENDIX


def test_compile_se_official_act_ops_emits_renumber_plus_mixed_section_family() -> None:
    act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) med instruktion för Socialstyrelsen",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) med instruktion för Socialstyrelsen "
            "dels att nuvarande 17 § ska betecknas 16 §, dels att 9 och 10 §§, den nya 16 §, 18 och 23 §§ "
            "ska ha följande lydelse, dels att det ska införas fem nya paragrafer, 6 a, 17, 18 a, 18 b och 22 §§, "
            "av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [
            {"label": "6a", "text": "Ny 6 a §."},
            {"label": "9", "text": "Ny 9 §."},
            {"label": "10", "text": "Ny 10 §."},
            {"label": "16", "text": "Ny 16 §."},
            {"label": "17", "text": "Ny 17 §."},
            {"label": "18", "text": "Ny 18 §."},
            {"label": "18a", "text": "Ny 18 a §."},
            {"label": "18b", "text": "Ny 18 b §."},
            {"label": "22", "text": "Ny 22 §."},
            {"label": "23", "text": "Ny 23 §."},
        ],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2018:1381")

    assert ops[0].action is StructuralAction.RENUMBER
    assert ops[0].target.path == (("section", "17"),)
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "16"),)
    assert [op.target.path for op in ops[1:]] == [
        (("section", "6a"),),
        (("section", "9"),),
        (("section", "10"),),
        (("section", "16"),),
        (("section", "17"),),
        (("section", "18"),),
        (("section", "18a"),),
        (("section", "18b"),),
        (("section", "22"),),
        (("section", "23"),),
    ]
    assert [op.action.value for op in ops[1:]] == [
        "insert",
        "replace",
        "replace",
        "replace",
        "insert",
        "replace",
        "insert",
        "insert",
        "insert",
        "replace",
    ]


def test_compile_se_official_act_ops_records_renumber_arity_mismatch() -> None:
    act = {
        "sfs_id": "2026:777",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2026:106) om något "
            "dels att nuvarande 2 och 3 §§ ska betecknas 4 §, "
            "dels att 4 § ska ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["4"],
        "provisions": [{"label": "4", "text": "Ny lydelse."}],
        "signatories": [],
        "footnotes": [],
    }
    adjudications: list[CompileAdjudication] = []

    ops = compile_se_official_act_ops(act, source_id="2026:777", adjudications_out=adjudications)

    assert [op.action for op in ops] == [StructuralAction.REPLACE]
    assert [op.target.path for op in ops] == [(("section", "4"),)]
    mismatch = [
        adjudication
        for adjudication in adjudications
        if adjudication.detail["rule_id"] == "se_official_clause_renumber_arity_mismatch"
    ]
    assert len(mismatch) == 1
    assert mismatch[0].kind == "se_official_clause_surface_skipped"
    assert mismatch[0].detail["phase"] == "parse"
    assert mismatch[0].detail["source_labels"] == ("2", "3")
    assert mismatch[0].detail["destination_labels"] == ("4",)
    assert mismatch[0].detail["strict_disposition"] == "block"


def test_compile_se_official_act_ops_records_unclaimed_payload() -> None:
    act = {
        "sfs_id": "2026:778",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2026:106) om något "
            "dels att det ska införas en ny paragraf, 7 a §, av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": [],
        "provisions": [{"label": "7a", "text": "Ny lydelse."}],
        "inserted_headings": [{"before_label": "9", "text": "Rubrik utan stöd i klausul"}],
        "appendices": [{"label": "", "title": "Bilaga utan stöd", "text": "Bilagetext"}],
        "signatories": [],
        "footnotes": [],
    }
    adjudications: list[CompileAdjudication] = []

    ops = compile_se_official_act_ops(act, source_id="2026:778", adjudications_out=adjudications)

    assert [op.action for op in ops] == [StructuralAction.INSERT]
    unclaimed = [
        adjudication
        for adjudication in adjudications
        if adjudication.kind == "se_official_unclaimed_payload_skipped"
    ]
    assert [adjudication.detail["payload_kind"] for adjudication in unclaimed] == [
        "inserted_heading",
        "appendix",
    ]
    assert [adjudication.detail["payload_label"] for adjudication in unclaimed] == ["9", ""]
    assert all(
        adjudication.detail["rule_id"] == "se_official_effect_plan_unclaimed_payload"
        for adjudication in unclaimed
    )
    assert all(adjudication.detail["strict_disposition"] == "block" for adjudication in unclaimed)


def test_compile_se_official_act_ops_emits_repeal_renumber_replace_insert_family() -> None:
    act = {
        "sfs_id": "2026:63",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2026-02-10",
        "issued_date": "2026-02-05",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att 16, 17 och 22 §§ ska upphöra att gälla, "
            "dels att nuvarande 17 a, 17 b och 22 a §§ ska betecknas 16, 17 och 22 §§, "
            "dels att 3, 4, 11 och 21 §§ ska ha följande lydelse, "
            "dels att det ska införas två nya paragrafer, 5 a och 15 b §§, av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2026.",
        "affected_section_labels": ["16", "17", "22"],
        "provisions": [
            {"label": "3", "text": "Ny 3 §."},
            {"label": "4", "text": "Ny 4 §."},
            {"label": "5a", "text": "Ny 5 a §."},
            {"label": "11", "text": "Ny 11 §."},
            {"label": "15b", "text": "Ny 15 b §."},
            {"label": "21", "text": "Ny 21 §."},
        ],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2026:63")

    assert [op.action.value for op in ops[:3]] == ["repeal", "repeal", "repeal"]
    assert [op.target.path for op in ops[:3]] == [
        (("section", "16"),),
        (("section", "17"),),
        (("section", "22"),),
    ]
    assert [(op.target.path, op.destination.path if op.destination else None) for op in ops[3:6]] == [
        ((("section", "22a"),), (("section", "22"),)),
        ((("section", "17b"),), (("section", "17"),)),
        ((("section", "17a"),), (("section", "16"),)),
    ]
    assert [op.action.value for op in ops[6:]] == ["replace", "replace", "insert", "replace", "insert", "replace"]


def test_compile_se_official_act_ops_emits_repeal_heading_insert_family() -> None:
    act = {
        "sfs_id": "2018:11",
        "title": "Förordning om ändring i förordningen (2015:284) med instruktion för Socialstyrelsen",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-01-30",
        "issued_date": "2018-01-18",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) med instruktion för Socialstyrelsen "
            "dels att 16 och 22 §§ ska upphöra att gälla, "
            "dels att det ska införas en ny paragraf, 7 a §, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2018.",
        "affected_section_labels": ["16", "22"],
        "provisions": [{"label": "7a", "text": "Ny 7 a §."}],
        "inserted_headings": [{"before_label": "7a", "text": "Donationsfrågor"}],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2018:11")

    assert [op.action.value for op in ops] == ["repeal", "repeal", "insert", "insert"]
    assert [op.target.path for op in ops[:2]] == [
        (("section", "16"),),
        (("section", "22"),),
    ]
    assert ops[2].target.path == (("section", "7a"),)
    assert ops[3].target.path == (("section", "7a"),)
    assert ops[3].target.special == FacetKind.HEADING


def test_compile_se_official_act_ops_surfaces_planned_effect_missing_payload() -> None:
    surface = SEOfficialClauseSurface(
        sfs_id="2026:999",
        title="Förordning om ändring i förordningen (2026:106) om något",
        amended_act_sfs_id="2026:106",
        is_amending_act=True,
        enacting_clause="Regeringen föreskriver att 2 § ska ha följande lydelse.",
        replace_section_labels=("2",),
        effective_date="2026-04-15",
    )
    plan = SEOfficialEffectsPlan(
        sfs_id="2026:999",
        title=surface.title,
        amended_act_sfs_id="2026:106",
        is_amending_act=True,
        effective_date="2026-04-15",
        elaboration=SEOfficialElaboratedIntent(
            clause_surface=surface,
            payload_surface=SEOfficialPayloadSurface(),
            issued_date="2026-03-19",
        ),
        frontier_classification="supported",
        planned_items=(
            SEOfficialEffectPlanItem(
                kind="replace_section",
                target_label="2",
                payload_label="2",
            ),
        ),
        planned_operation_count=1,
    )
    adjudications: list[CompileAdjudication] = []

    ops = _lower_se_official_effects_plan(plan, source_id="2026:999", adjudications_out=adjudications)

    assert ops == []
    assert len(adjudications) == 1
    assert adjudications[0].kind == "se_official_effect_lowering_skipped"
    assert adjudications[0].source_statute == "2026:999"
    assert adjudications[0].detail["rule_id"] == "se_official_effect_payload_not_found"
    assert adjudications[0].detail["phase"] == "lowering"
    assert adjudications[0].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0].detail["blocking"] is True
    assert adjudications[0].detail["strict_disposition"] == "block"
    assert adjudications[0].detail["quirks_disposition"] == "record"
    assert adjudications[0].detail["item_kind"] == "replace_section"
    assert adjudications[0].detail["target_label"] == "2"


def test_compile_se_official_ops_to_archive_stores_json_array() -> None:
    archive = _FakeArchive(
        stored={
            se_official_act_locator("2026:286"): json.dumps(
                {
                    "sfs_id": "2026:286",
                    "title": "Förordning om ändring i förordningen (2026:106) om något",
                    "act_type": "förordning",
                    "amended_act_sfs_id": "2026:106",
                    "is_amending_act": True,
                    "published_date": "2026-03-24",
                    "issued_date": "2026-03-19",
                    "enacting_clause": "Regeringen föreskriver att 2 och 8 §§ förordningen (2026:106) om något ska ha följande lydelse.",
                    "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
                    "affected_section_labels": ["2", "8"],
                    "provisions": [
                        {"label": "2", "text": "Första bestämmelsen."},
                        {"label": "8", "text": "Andra bestämmelsen."},
                    ],
                    "signatories": [],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        }
    )

    ops = compile_se_official_ops_to_archive(archive, "2026:286")

    assert len(ops) == 2
    assert se_official_clause_surface_locator("2026:286") in archive.stored
    assert se_official_payload_surface_locator("2026:286") in archive.stored
    assert se_official_elaboration_locator("2026:286") in archive.stored
    assert se_official_effects_plan_locator("2026:286") in archive.stored
    assert se_official_ops_locator("2026:286") in archive.stored
    assert se_official_ops_adjudications_locator("2026:286") in archive.stored
    assert load_se_official_clause_surface_from_archive(archive, "2026:286") is not None
    assert load_se_official_payload_surface_from_archive(archive, "2026:286") is not None
    assert load_se_official_elaboration_from_archive(archive, "2026:286") is not None
    assert load_se_official_effects_plan_from_archive(archive, "2026:286") is not None
    assert load_se_official_ops_adjudications_from_archive(archive, "2026:286") == []
    loaded = load_se_official_ops_from_archive(archive, "2026:286")
    assert loaded is not None
    assert loaded[0]["action"] == "replace"


def test_compile_se_official_ops_to_archive_stores_unsupported_plan_adjudication() -> None:
    text = (
        "Svensk författningssamling\n"
        "Lag\n"
        "om ändring i lagen (1988:950) om kulturminnen m.m.\n\n"
        "Publicerad\n"
        "den 20 december 2002\n\n"
        "Utfärdad den 19 december 2002\n"
        "Enligt riksdagens beslut föreskrivs att bilagan till lagen (1988:950) om kulturminnen m.m. "
        "skall ha följande lydelse.\n"
        "Denna lag träder i kraft den 1 januari 2003.\n"
    )
    act = parse_se_official_act_text(text, sfs_id="2002:1090")
    archive = _FakeArchive(
        stored={
            se_official_act_locator("2002:1090"): json.dumps(
                se_official_act_text_to_dict(act),
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    with pytest.raises(NotImplementedError, match="has no planned canonical effects"):
        compile_se_official_ops_to_archive(archive, "2002:1090")

    effects_plan = load_se_official_effects_plan_from_archive(archive, "2002:1090")
    adjudications = load_se_official_ops_adjudications_from_archive(archive, "2002:1090")

    assert effects_plan is not None
    assert effects_plan["frontier_classification"] == "empty_effect_plan_with_clause_targets"
    assert se_official_ops_locator("2002:1090") not in archive.stored
    assert se_official_ops_adjudications_locator("2002:1090") in archive.stored
    assert adjudications is not None
    assert len(adjudications) == 1
    assert adjudications[0]["kind"] == "se_official_effect_plan_unsupported"
    assert adjudications[0]["source_statute"] == "2002:1090"
    assert adjudications[0]["detail"]["rule_id"] == "se_official_effect_plan_unsupported"
    assert adjudications[0]["detail"]["phase"] == "lowering"
    assert adjudications[0]["detail"]["strict_disposition"] == "block"
    assert adjudications[0]["detail"]["frontier_detail"] == "appendix_clause_only_unlabeled"


def test_load_se_official_ops_rejects_non_object_entries() -> None:
    archive = _FakeArchive(
        stored={
            se_official_ops_locator("2026:286"): json.dumps(
                [{"action": "replace"}, "silently-dropped-before", 42],
                ensure_ascii=False,
            ).encode("utf-8")
        }
    )

    with pytest.raises(ValueError, match="non-object op entries at indexes: 1, 2"):
        load_se_official_ops_from_archive(archive, "2026:286")


def test_load_se_backfill_official_history_rejects_non_object_entries() -> None:
    archive = _FakeArchive(
        stored={
            se_backfill_official_history_locator(): json.dumps(
                [{"sfs_id": "2026:286"}, "silently-dropped-before", 42],
                ensure_ascii=False,
            ).encode("utf-8")
        }
    )

    with pytest.raises(ValueError, match="non-object entries at indexes: 1, 2"):
        load_se_backfill_official_history_from_archive(archive)


def test_build_se_official_effects_plan_records_planned_canonical_effects_without_lowering() -> None:
    act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2, 8 och 11 §§ förordningen (2026:106) om kriminalvårdens behandling av personuppgifter inom brottsdatalagens område ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2", "8", "11"],
        "provisions": [
            {"label": "2", "text": "Uppgifter om målsägande får göras gemensamt tillgängliga."},
            {"label": "8", "text": "Uppgifter i säkerhetsregistret får lämnas ut."},
            {"label": "11", "text": "Nedan angivna myndigheter ska lämna uppgifter.\n\n1. Polismyndigheten"},
        ],
        "signatories": [],
        "footnotes": [],
    }

    plan = _build_se_official_effects_plan(_build_se_official_elaboration(_coerce_official_act(act)))

    assert plan.sfs_id == "2026:286"
    assert plan.planned_operation_count == 3
    assert [item.kind for item in plan.planned_items] == ["replace_section", "replace_section", "replace_section"]
    assert [item.target_label for item in plan.planned_items] == ["2", "8", "11"]
    assert plan.frontier_classification == "supported"


def test_build_se_official_effects_plan_recovers_section_word_substitution() -> None:
    act = {
        "sfs_id": "2002:1162",
        "title": "Förordning om ändring i förordningen (1991:978) om statsbidrag till produktion av vissa läromedel",
        "act_type": "förordning",
        "amended_act_sfs_id": "1991:978",
        "is_amending_act": True,
        "published_date": "2002-12-19",
        "issued_date": "2002-12-19",
        "enacting_clause": "Regeringen föreskriver att i 2 och 6 §§ förordningen (1991:978) om statsbidrag till produktion av vissa läromedel ordet ”Skolverket” skall bytas ut mot ”Myndigheten för skolutveckling”.",
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2003.",
        "affected_section_labels": ["2", "6"],
        "provisions": [],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    plan = _build_se_official_effects_plan(_build_se_official_elaboration(_coerce_official_act(act)))

    assert plan.sfs_id == "2002:1162"
    assert plan.planned_operation_count == 2
    assert [item.kind for item in plan.planned_items] == ["text_replace", "text_replace"]
    assert [item.target_label for item in plan.planned_items] == ["2", "6"]
    assert all(item.text_patch is not None for item in plan.planned_items)
    assert [item.text_patch.selector.match_text for item in plan.planned_items if item.text_patch] == [
        "Skolverket",
        "Skolverket",
    ]
    assert [item.text_patch.replacement for item in plan.planned_items if item.text_patch] == [
        "Myndigheten för skolutveckling",
        "Myndigheten för skolutveckling",
    ]
    assert plan.frontier_classification == "supported"
    assert plan.frontier_detail == ""


def test_build_se_official_effects_plan_recovers_point_word_substitution() -> None:
    text = """Svensk författningssamling
Förordning
om ändring i förordningen (1999:1424) om
förvaltning av EG:s strukturfonder;

Utkom från trycket
den 12 december 2000

utfärdad den 30 november 2000.
Regeringen föreskriver att i punkt 3 i ikraftträdande- och övergångsbestämmelserna till förordningen (1999:1424) om förvaltning av EG:s strukturfonder ordet ”NUTEK” skall bytas ut mot ”Verket för näringslivsutveckling”.
Denna förordning träder i kraft den 1 januari 2001.
På regeringens vägnar
ULRICA MESSING
    """
    act = parse_se_official_act_text(text, "2000:1143")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))

    assert plan.sfs_id == "2000:1143"
    assert plan.planned_operation_count == 1
    assert [item.kind for item in plan.planned_items] == ["text_replace"]
    assert [item.target_label for item in plan.planned_items] == ["3"]
    assert all(item.text_patch is not None for item in plan.planned_items)
    assert [item.text_patch.selector.match_text for item in plan.planned_items if item.text_patch] == ["NUTEK"]
    assert [item.text_patch.replacement for item in plan.planned_items if item.text_patch] == [
        "Verket för näringslivsutveckling"
    ]
    assert plan.frontier_classification == "supported"
    assert plan.frontier_detail == ""


def test_parse_se_official_act_text_recovers_single_replace_label_payload_without_section_markers() -> None:
    text = """Svensk författningssamling
SFS 2002:639

Förordning
om ändring i kungörelsen (1971:831) om
pensionsbelopp enligt 10 kap. 2 § första stycket
lagen (1962:381) om allmän försäkring;

Utkom från trycket
den 2 juli 2002

utfärdad den 19 juni 2002.
Regeringen föreskriver att kungörelsen (1971:831) om pensionsbelopp
enligt 10 kap. 2 § första stycket lagen (1962:381) om allmän försäkring 1
skall ha följande lydelse.
Folkpension som avses i 10 kap. 2 § första stycket lagen (1962:381) om
allmän försäkring skall utges med högst ett belopp som motsvarar 30 procent av folkpensionen för en ogift ålderspensionär enligt 6 kap. 2 § första
stycket lagen om allmän försäkring i dess lydelse före den 1 januari 2001
jämte pensionstillskott enligt 2 § första stycket lagen (1969:205) om pensionstillskott.
Uppkommer under någon av månaderna augusti–december 2002 en sådan
omständighet som medför att första stycket skall tillämpas på folkpension
enligt 7 kap. lagen om allmän försäkring, skall beräkning enligt nämnda
stycke ske på följande sätt:
– uppkommer omständigheten i augusti skall procenttalet 30 i första
stycket bytas ut mot talet 40,
– uppkommer omständigheten i september skall procenttalet 30 i första
stycket bytas ut mot talet 60,
– uppkommer omständigheten i oktober skall procenttalet 30 i första
stycket bytas ut mot talet 90, och
– uppkommer omständigheten i november eller december skall procenttalet 30 i första stycket bytas ut mot talet 100.
Om den pensionsberättigade på grund av sitt tillstånd uppenbarligen inte
kan tillgodogöra sig detta belopp till sin personliga nytta, får Riksförsäkringsverket besluta att sätta ned beloppet, dock till lägst 500 kronor per år.
Denna förordning träder i kraft den 1 augusti 2002.
På regeringens vägnar
INGELA THALÉN
Marianne Jenryd
(Socialdepartementet)
1

Senaste lydelse 2000:1052.

1
"""

    act = parse_se_official_act_text(text, "2002:639")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:639")

    assert [provision.label for provision in act.provisions] == ["2"]
    assert "Folkpension som avses" in act.provisions[0].text
    assert plan.sfs_id == "2002:639"
    assert plan.planned_operation_count == 1
    assert [item.kind for item in plan.planned_items] == ["replace_section"]
    assert [item.target_label for item in plan.planned_items] == ["2"]
    assert plan.frontier_classification == "supported"
    assert plan.frontier_detail == ""
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.leaf_label() == "2"


def test_parse_se_official_act_text_recovers_inserted_point_payload_without_section_markers() -> None:
    text = """Svensk författningssamling
Förordning om ändring i förordningen (1988:882) om ändring i lagen (1976:380) om arbetsskadeförsäkring
Utkom från trycket den 30 december 2002
Utfärdad den 2002-12-19

Enligt riksdagens beslut1 föreskrivs att det i övergångsbestämmelserna till förordningen (1988:882) om ändring i lagen (1976:380) om arbetsskadeförsäkring skall införas en ny punkt, 10, av följande lydelse. 10. Vid tillämpning av 6 kap. 1 § i dess äldre lydelse skall med folkpension i form av ålderspension och tilläggspension i form av änkepension likställas garantipension enligt lagen (1998:702) om garantipension.
Denna förordning träder i kraft den 1 mars 2003.
På regeringens vägnar
"""

    act = parse_se_official_act_text(text, "2002:1071")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:1071")

    assert [provision.label for provision in act.provisions] == ["10"]
    assert "garantipension" in act.provisions[0].text
    assert plan.planned_operation_count == 1
    assert plan.frontier_classification == "supported"
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.leaf_label() == "10"


def test_parse_se_official_act_text_recovers_mixed_point_payloads_without_section_markers() -> None:
    text = """Svensk författningssamling
Förordning om ändring i lagen (1987:813) om homosexuella sambor
Utkom från trycket den 30 december 2002
Utfärdad den 2002-12-19

Enligt riksdagens beslut1 föreskrivs i fråga om lagen (1987:813) om homosexuella sambor2 dels att punkt 23 skall ha följande lydelse, dels att det i lagen skall införas en ny punkt, punkt 24, av följande lydelse. Om två personer bor tillsammans i ett homosexuellt förhållande, skall vad som gäller i fråga om sambor enligt följande lagar och bestämmelser tillämpas även på de homosexuella samborna: 22. 12 § första stycket 2 lagen (2001:82) om svenskt medborgarskap, 23. lagen (1990:272) om internationella frågor rörande makars och sambors förmögenhetsförhållanden, 24. 4 kap. 2 § andra stycket a) lagen (1962:381) om allmän försäkring.
Denna lag träder i kraft den 1 januari 2003.
På regeringens vägnar
"""

    act = parse_se_official_act_text(text, "2002:604")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:604")

    assert [provision.label for provision in act.provisions] == ["22", "23", "24"]
    assert plan.planned_operation_count == 3
    assert plan.frontier_classification == "supported"
    assert [op.action.value for op in ops] == ["replace", "replace", "insert"]
    assert [op.target.leaf_label() for op in ops] == ["22", "23", "24"]


def test_compile_se_official_act_ops_replays_word_substitution_sections() -> None:
    act = {
        "sfs_id": "2002:1162",
        "title": "Förordning om ändring i förordningen (1991:978) om statsbidrag till produktion av vissa läromedel",
        "act_type": "förordning",
        "amended_act_sfs_id": "1991:978",
        "is_amending_act": True,
        "published_date": "2002-12-19",
        "issued_date": "2002-12-19",
        "enacting_clause": "Regeringen föreskriver att i 2 och 6 §§ förordningen (1991:978) om statsbidrag till produktion av vissa läromedel ordet ”Skolverket” skall bytas ut mot ”Myndigheten för skolutveckling”.",
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2003.",
        "affected_section_labels": ["2", "6"],
        "provisions": [],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }
    statute = IRStatute(
        statute_id="1991:978",
        title="Förordning (1991:978) om statsbidrag till produktion av vissa läromedel",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Skolverket prövar ansökan."),),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Skolverket meddelar beslut."),),
                ),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="8",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Oändrad lydelse."),),
                ),
            ),
        ),
        supplements=(),
        metadata={},
    )

    ops = compile_se_official_act_ops(act, source_id="2002:1162")
    replayed = apply_se_ops(statute, ops)
    section_map = se_section_text_map(replayed)

    assert [op.action.value for op in ops] == ["text_patch", "text_patch"]
    assert [op.target.leaf_label() for op in ops] == ["2", "6"]
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch is not None for op in ops)
    assert section_map["2"] == "Myndigheten för skolutveckling prövar ansökan."
    assert section_map["6"] == "Myndigheten för skolutveckling meddelar beslut."
    assert section_map["8"] == "Oändrad lydelse."


def test_parse_se_official_act_text_recovers_single_point_payload_without_section_markers() -> None:
    text = """Svensk författningssamling
Lag om ändring i lagen (1987:813) om homosexuella sambor
Utkom från trycket den 30 december 2002
Utfärdad den 2002-12-19

Enligt riksdagens beslut1 föreskrivs att punkten 21 i lagen (1987:813) om homosexuella sambor2 skall ha följande lydelse. Om två personer bor tillsammans i ett homosexuellt förhållande, skall vad som gäller i fråga om sambor enligt följande lagar och bestämmelser tillämpas även på de homosexuella samborna:
Denna lag träder i kraft den 15 januari 2003.
På regeringens vägnar
"""

    act = parse_se_official_act_text(text, "2002:1114")
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    ops = compile_se_official_act_ops(se_official_act_text_to_dict(act), source_id="2002:1114")

    assert act.affected_section_labels == ("21",)
    assert [provision.label for provision in act.provisions] == ["21"]
    assert "homosexuellt förhållande" in act.provisions[0].text
    assert plan.planned_operation_count == 1
    assert plan.frontier_classification == "supported"
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.leaf_label() == "21"


def test_compile_se_official_act_ops_supports_old_style_repeal_spelling() -> None:
    act = {
        "sfs_id": "2002:1163",
        "title": "Förordning om ändring i förordningen (1991:1195) om det offentliga skolväsendet under krig och vid krigsfara m.m.",
        "act_type": "förordning",
        "amended_act_sfs_id": "1991:1195",
        "is_amending_act": True,
        "published_date": "2002-12-19",
        "issued_date": "2002-12-19",
        "enacting_clause": "Regeringen föreskriver att 15 § förordningen (1991:1195) om det offentliga skolväsendet under krig och vid krigsfara m.m. skall upphöra att gälla vid utgången av februari 2003.",
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2003.",
        "affected_section_labels": [],
        "provisions": [],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2002:1163")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.leaf_label() == "15"


def test_se_expiry_boundary_extraction_covers_end_of_month_year_and_explicit() -> None:
    # "vid utgången av <month> <year>" -> last calendar day of that month.
    assert (
        extract_se_expiry_boundary_date("15 § skall upphöra att gälla vid utgången av februari 2003")
        == "2003-02-28"
    )
    # Leap-year February.
    assert (
        extract_se_expiry_boundary_date("upphöra att gälla vid utgången av februari 2004")
        == "2004-02-29"
    )
    # "vid utgången av <year>" -> 31 december.
    assert extract_se_expiry_boundary_date("upphöra att gälla vid utgången av 2005") == "2005-12-31"
    # Explicit "den <day> <month> <year>".
    assert extract_se_expiry_boundary_date("upphör att gälla den 1 mars 2003") == "2003-03-01"
    # Open-ended cease-to-apply -> no explicit boundary (keyed off commencement).
    assert extract_se_expiry_boundary_date("16 och 22 §§ ska upphöra att gälla,") == ""


def test_compile_se_official_act_ops_lowers_upphora_att_galla_as_temporal_expiry() -> None:
    # §2.1/§6.4 (#186): a dated "upphöra att gälla" is a TEMPORAL EXPIRY, not a
    # structural repeal. The tree action stays REPEAL (the section leaves the
    # in-force surface, matching the oracle), but the op is first-classed as an
    # expiry: TOMBSTONE(expired) not (repealed), and a companion EXPIRY
    # TemporalEvent carries the ``expires`` boundary on the timeline rail.
    act = {
        "sfs_id": "2002:1163",
        "title": "Förordning om ändring i förordningen (1991:1195) om det offentliga skolväsendet",
        "act_type": "förordning",
        "amended_act_sfs_id": "1991:1195",
        "is_amending_act": True,
        "published_date": "2002-12-19",
        "issued_date": "2002-12-19",
        "enacting_clause": "Regeringen föreskriver att 15 § förordningen (1991:1195) skall upphöra att gälla vid utgången av februari 2003.",
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2003.",
        "affected_section_labels": [],
        "provisions": [],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2002:1163")
    assert len(ops) == 1
    op = ops[0]

    # Tree action unchanged (oracle-agreement mechanics preserved).
    assert op.action is StructuralAction.REPEAL
    assert op.target.leaf_label() == "15"
    # Modelled as a temporal expiry: TOMBSTONE(expired), not (repealed).
    assert se_op_is_temporal_expiry(op) is True
    assert se_op_tombstone_disposition(op) == "temporary_expiry"
    assert "se_temporal_expiry=1" in op.provenance_tags
    assert "expiry_boundary_inclusive=2003-02-28" in op.provenance_tags
    # Prose-inclusive Feb 28 -> exclusive kernel cutoff Mar 1.
    assert "expires=2003-03-01" in op.provenance_tags

    # Companion EXPIRY TemporalEvent on the timeline rail, scoped to the section.
    event = se_temporal_expiry_event(op, base_statute_id="1991:1195")
    assert event is not None
    assert event.kind == "expire"
    assert event.expires == "2003-03-01"
    assert event.scope.target_statute == "1991:1195"
    assert [addr.leaf_label() for addr in event.scope.exact_addresses] == ["15"]

    events = se_temporal_events_for_ops(ops, base_statute_id="1991:1195")
    assert len(events) == 1
    assert events[0].kind == "expire"


def test_se_open_ended_upphora_att_galla_is_expiry_without_boundary_date() -> None:
    # An "upphöra att gälla" with no explicit date is still a temporal expiry
    # (TOMBSTONE(expired)); the boundary is the amending act's own commencement,
    # so the EXPIRY event carries an empty ``expires`` string rather than a date.
    act = {
        "sfs_id": "2026:63",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2026-02-10",
        "issued_date": "2026-02-05",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att 16 och 22 §§ ska upphöra att gälla, "
            "dels att 3 § ska ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2026.",
        "affected_section_labels": ["16", "22"],
        "provisions": [{"label": "3", "text": "Ny 3 §."}],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2026:63")
    repeal_ops = [op for op in ops if op.action is StructuralAction.REPEAL]
    assert [op.target.leaf_label() for op in repeal_ops] == ["16", "22"]
    for op in repeal_ops:
        assert se_op_tombstone_disposition(op) == "temporary_expiry"
        assert "expires=" not in "".join(op.provenance_tags)  # open-ended
        event = se_temporal_expiry_event(op, base_statute_id="2015:284")
        assert event is not None and event.kind == "expire" and event.expires == ""
    # The REPLACE op (genuine content change, not an expiry) is not tagged.
    replace_ops = [op for op in ops if op.action is StructuralAction.REPLACE]
    assert replace_ops
    for op in replace_ops:
        assert se_op_is_temporal_expiry(op) is False
        assert se_op_tombstone_disposition(op) == ""


def test_compile_se_official_act_ops_infers_base_act_from_enacting_clause() -> None:
    act = {
        "sfs_id": "2002:1150",
        "title": "Förordning om ändring i rättsinformationsförordningen",
        "act_type": "förordning",
        "amended_act_sfs_id": "",
        "is_amending_act": True,
        "published_date": "2002-12-20",
        "issued_date": "2002-12-20",
        "enacting_clause": "Regeringen föreskriver att 15 § rättsinformationsförordningen (1999:175) skall ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 1 januari 2003.",
        "affected_section_labels": ["15"],
        "provisions": [{"label": "15", "text": "Rättsinformationsförordningen ska gälla."}],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    ops = compile_se_official_act_ops(act, source_id="2002:1150")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].provenance_tags[1] == "base_sfs_id=1999:175"


def test_materialize_se_statute_as_of_selects_temporal_section_variant() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2026:286",
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-04-15/\n"
                "Gamla lydelsen.\n\n"
                "2 § /Träder i kraft I:2026-04-15/\n"
                "Nya lydelsen.\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(payload)

    pre = materialize_se_statute_as_of(statute, "2026-04-14")
    post = materialize_se_statute_as_of(statute, "2026-04-15")

    assert se_section_text_map(pre)["2"] == "Gamla lydelsen."
    assert se_section_text_map(post)["2"] == "Nya lydelsen."


def test_parse_se_statute_records_orphan_temporal_marker_without_leaking_to_next_section() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "/Träder i kraft I:2026-04-15/\n\n"
                "Rubrik utan paragraf\n\n"
                "2 § Text som inte ska ärva markören."
            ),
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }

    statute = parse_se_statute(payload)

    diagnostics = statute.metadata["source_diagnostics"]
    assert diagnostics == (
        {
            "rule_id": "se_current_text_orphan_temporal_marker_skipped",
            "family": "source_pathology",
            "phase": "extraction",
            "reason": (
                "Sweden current-text parser skipped a marker-only temporal block because "
                "the following block was not a section that could own it."
            ),
            "sfs_id": "2026:106",
            "block_index": 1,
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "marker_text": "Träder i kraft 2026-04-15",
            "next_block_text": "Rubrik utan paragraf",
        },
    )
    section = next(child for child in statute.body.children if child.kind is IRNodeKind.SECTION)
    assert "RestrictStartDate" not in section.attrs
    assert "TemporalMarkers" not in section.attrs


def test_apply_se_ops_replaces_section_text_on_materialized_base() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2026:286",
            "forfattningstext": "2 § Gamla lydelsen.\n\n8 § Också gammal lydelse.",
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2 och 8 §§ förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2", "8"],
        "provisions": [
            {"label": "2", "text": "Nya lydelsen."},
            {"label": "8", "text": "Också ny lydelse."},
        ],
        "signatories": [],
        "footnotes": [],
    }

    statute = parse_se_statute(payload)
    ops = compile_se_official_act_ops(act, source_id="2026:286")
    replayed = apply_se_ops(statute, ops)

    section_map = se_section_text_map(replayed)
    assert section_map["2"] == "Nya lydelsen."
    assert section_map["8"] == "Också ny lydelse."
    assert replayed.metadata["applied_op_count"] == 2


def test_apply_se_ops_records_replay_failures_as_adjudications() -> None:
    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Ursprunglig 2 §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        LegalOperation(
            op_id="replace-section-no-payload",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=None,
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="replace-section-missing",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="Nytt innehåll."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="insert-section-existing",
            sequence=3,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny befintlig text."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="unsupported-target-kind",
            sequence=4,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("article", "3"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="3", text="Ny artikel."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="unsupported-section-action",
            sequence=5,
            action=StructuralAction.TEXT_PATCH,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny text."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="appendix-replace-missing",
            sequence=6,
            action=StructuralAction.REPLACE,
            # The bilaga/appendix lives in the ``supplements`` compartment root
            # (§5.3 / §7 delta #6): the SE materializer selects the supplements
            # resolution lane off the address ``root``, exactly as the production
            # mint site (``_lower_se_official_effect_plan_item``) now stamps
            # ``root="supplements"``. Without it the op would resolve against the
            # statute ``body`` compartment (unsupported target kind).
            target=LegalAddress(path=(("appendix", "A"),), root="supplements"),
            payload=IRNode(kind=IRNodeKind.APPENDIX, label="A", text="Bilaga A."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    replayed = apply_se_ops(statute, ops, adjudications_out=adjudications)

    assert [adj.kind for adj in adjudications] == [
        "se_replay_payload_missing",
        "se_replay_target_not_found",
        "se_replay_unsupported_action",
        "se_replay_unsupported_target_kind",
        "se_replay_payload_missing",
        "se_replay_target_not_found",
    ]
    assert adjudications[0].op_id == "replace-section-no-payload"
    assert adjudications[0].detail["rule_id"] == "se_replay_payload_missing"
    assert adjudications[0].detail["phase"] == "replay"
    assert adjudications[1].detail["target"] == "9"
    assert adjudications[2].detail["rule_id"] == "se_replay_unsupported_action"
    assert adjudications[2].detail["family"] == "unsupported_or_unresolved_action"
    assert adjudications[2].detail["blocking"] is True
    assert adjudications[2].detail["strict_disposition"] == "block"
    assert adjudications[2].detail["quirks_disposition"] == "record"
    assert adjudications[3].source_statute == "2026:999"
    assert adjudications[3].detail["target_kind"] == "article"
    assert replayed.metadata["applied_op_count"] == 0


def test_apply_se_ops_conserved_returns_typed_filter_result_partition() -> None:
    """Typed conservation receipt (AGENTS.md §1.8): every op ends up in exactly one lane.

    The classic :func:`apply_se_ops` returns only the replayed IRStatute and
    shuttles skipped-op evidence through an ``adjudications_out`` out-param;
    a consumer that doesn't pass one silently loses track of which ops were
    filtered. :func:`apply_se_ops_conserved` returns a typed
    :class:`SEApplyResult` whose ``filter_result`` partitions every input op
    into ``accepted_items`` (its binding landed in the output statute) or
    ``rejected_items`` (:class:`RejectedItem[LegalOperation]` witness). Every
    input op MUST land in exactly one lane — never silently dropped.
    """
    from lawvm.core.filter_result import FilterResult, RejectedItem

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Ursprunglig 2 §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(json.dumps(payload).encode("utf-8"))

    ops: list[LegalOperation] = [
        # op #1 — succeeds: REPLACE §2 with a valid section payload
        LegalOperation(
            op_id="replace-section-ok",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny lydelse."),
            source=OperationSource(statute_id="2026:999"),
        ),
        # op #2 — skipped: REPLACE §9, target not found in the statute body
        LegalOperation(
            op_id="replace-section-missing",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="Nytt innehåll."),
            source=OperationSource(statute_id="2026:999"),
        ),
        # op #3 — skipped: INSERT §2, target already exists (replay won't hijack)
        LegalOperation(
            op_id="insert-section-existing",
            sequence=3,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny befintlig text."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    result = apply_se_ops_conserved(statute, ops)

    # The returned statute IS the replayed IRStatute — §2's text was replaced.
    assert "Ny lydelse" in result.statute.body.children[0].text

    # Conservation contract: every input op appears in exactly one lane.
    # No silent drops, no phantom duplicates.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "replace-section-ok"

    assert len(result.skipped_items) == 2
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "replace-section-missing" in rejected_by_id
    assert "insert-section-existing" in rejected_by_id

    # Each RejectedItem carries the typed witness: reason, reason_code, blocking.
    for item in result.skipped_items:
        assert isinstance(item, RejectedItem)
        assert item.reason
        assert item.reason_code
        assert item.blocking is False  # SE skips are recorded, not blocking

    # The filter_result is a contract FilterResult[LegalOperation]
    assert isinstance(result.filter_result, FilterResult)
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    # Exactly the input — partition is total (no silent drops, no phantoms).
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint

    # Adjudications are also forwarded when the caller passes an out-param
    # (the typed carrier does NOT replace the existing descriptive adjudication
    # path; both share the same evidence ledger).
    adjudications: list[CompileAdjudication] = []
    result_with_adj = apply_se_ops_conserved(statute, ops, adjudications_out=adjudications)
    assert len(adjudications) == 2  # the two skipped ops
    assert result_with_adj.applied_ops[0].op_id == result.applied_ops[0].op_id


def test_apply_se_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """Regression (§1.8 conservation): a SKIPPED op with an empty op_id must
    NOT silently land in the accepted lane.

    Before the fix, the partition keyed on the op_id string and the skipped set
    was ``{a.op_id for a in adjudications if a.op_id}`` — the ``if a.op_id``
    filter drops a skipped op whose op_id is the default ``""``, so the
    partition loop's ``op.op_id in skipped_op_ids`` was ``"" in set()`` ==
    False, and the SKIPPED op fell through to ``accepted`` — a silent drop of
    the rejection (violating "every input op lands in exactly one of
    accepted/rejected"). The fix fails loud on empty op_ids instead of
    mis-partitioning. (Empty op_ids cannot be a robust identity key.)
    """
    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "X", "namnOchEnhet": "X"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Ursprunglig 2 §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        # SKIPPED op carrying the DEFAULT empty op_id (target §9 not found).
        # Before the fix this op was filtered out of the skipped set and
        # silently landed in `accepted` — a §1.8 conservation violation.
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="Nytt."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    # The fix refuses to proceed (fail loud) rather than silently accepting a
    # rejected op — the conservation invariant cannot be honored with an
    # un-keyable identity, so the helper raises instead of dropping it.
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_se_ops_conserved(statute, ops)


def test_apply_se_ops_conserved_rejects_duplicate_op_ids() -> None:
    """Regression (§1.8 conservation): duplicate/shared op_ids mis-partition.

    Two distinct ops sharing the same op_id cannot be partitioned by the op_id
    string — if one is skipped, BOTH would be classed as skipped (or both
    accepted), breaking the per-op accepted/rejected bijection. The fix fails
    loud on duplicate op_ids rather than mis-partitioning.
    """
    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "X", "namnOchEnhet": "X"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Ursprunglig 2 §.\n\n3 § Tredje.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        LegalOperation(
            op_id="dup",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny 2."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="dup",  # shared id with op #1 — not a robust identity
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped (not found)
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="Ny 9."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_se_ops_conserved(statute, ops)


def test_se_observed_replay_audit_reports_clean_for_target_bounded_replace() -> None:
    """Observed-write-audit is clean when mutations stay inside the declared target.

    The audit compares actual before/after IR tree diffs against the ops'
    declared target regions (§2.3 receipt contract, §1.0 Mutation Boundary
    Invariant). When every changed path falls within one op's declared
    target, the audit status is ``clean`` — no mutation-boundary violation.
    """
    from lawvm.sweden.grafter import se_observed_replay_audit

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om audit test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Gamla 1 §.\n\n2 § Oförändrad 2 §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    before = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        LegalOperation(
            op_id="se_official_replace_2026:999_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Ny 1 §."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    result = apply_se_ops(before, ops)
    audit = se_observed_replay_audit(before, result, ops)

    assert audit.op_count == 1
    assert audit.is_clean is True
    assert audit.status == "clean"
    assert audit.unexplained_paths == ()
    # The observed changed paths include the §1 section node (replace touched it).
    assert len(audit.observed_changed_paths) > 0


def test_se_observed_replay_audit_flags_violation_for_unexplained_mutation() -> None:
    """Audit status is ``violation`` when a mutation falls outside the declared target.

    If a REPRICE op declares target=§1 but the replay also secretly changes §2's
    text (e.g., via an invisible heuristic that accidentally touches a sibling),
    the audit catches it: the changed §2 path is NOT within the §1 declared
    boundary, so ``unexplained_paths`` is non-empty and ``status == "violation"``.

    Regression: simulate an off-target mutation by running the audit against
    a before/after pair where §2 was manually altered to differ — the audit
    MUST flag it as a violation of §1.0 Mutation Boundary Invariant.
    """
    from lawvm.sweden.grafter import se_observed_replay_audit

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om audit test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Gamla 1 §.\n\n2 § Oförändrad 2 §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    before = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        LegalOperation(
            op_id="se_official_replace_2026:999_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Ny 1 §."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    # Apply the legitimate replace (target=§1, stays in boundary)
    result = apply_se_ops(before, ops)
    # Simulate an invisible mutation: manually alter §2's text in the result tree
    # so the audit catches it as an unexplained mutation outside the declared
    # boundary (§1's target region). The audit is the independent observer that
    # catches what the apply helper did NOT declare.
    section2_path = tree_ops.find(result.body, "section", "2")
    assert section2_path is not None
    section2_node = tree_ops.resolve(result.body, section2_path)
    assert section2_node is not None
    mutated_section2 = IRNode(
        kind=section2_node.kind,
        label=section2_node.label,
        text="Mutated 2 §.",
        attrs=dict(section2_node.attrs),
        children=section2_node.children,
    )
    contaminated_result = tree_ops.replace_at(result.body, section2_path, mutated_section2)
    from lawvm.core.ir import IRStatute as _IS
    contaminated_statute = _IS(
        statute_id=result.statute_id,
        title=result.title,
        body=contaminated_result,
        supplements=list(result.supplements),
        metadata=dict(result.metadata),
    )
    audit = se_observed_replay_audit(before, contaminated_statute, ops)

    assert audit.status == "violation"
    assert audit.unexplained_paths != ()
    # The unexplained path includes section:2 (which is NOT the declared §1 target)
    assert any(
        any(kind == "section" and label == "2" for kind, label in path)
        for path in audit.unexplained_paths
    ), audit.unexplained_paths


def test_se_replay_write_receipts_emits_typed_receipt_per_applied_op() -> None:
    """Per-op WriteReceipt emission (§2.3 receipt contract, second step).

    :func:`se_replay_write_receipts` applies ops one at a time, snapshots
    before/after body trees, and synthesizes a contract :class:`WriteReceipt`
    per applied op. The receipt carries the typed §2.3 fields: op_id /
    helper / action / bound_target_path / landed_primary_path / categorized
    mutation footprint (created/replaced/removed/renumbered) / pre & post
    structural subtree hashes.

    Regression (synthetic; exercises every op action family that the SE
    apply path supports):
      * REPLACE §1: receipt shows ``replaced_paths = (('section', '1'),)`` and
        both pre/post hashes non-empty (the section existed before AND after).
      * INSERT §3a: receipt shows ``created_paths = (('section', '3a'),)`` and
        pre_hash = "" / post_hash non-empty (the section was absent before,
        present after — the receipt's hash signal is the opposite of REPEAL).
      * REPEAL §2: receipt shows ``removed_paths = (('section', '2'),)`` and
        pre_hash non-empty / post_hash = "" (the section existed before,
        absent after).

    Skipped ops emit no receipt (the FilterResult's rejected_items lane
    carries them instead — the conservation partition + receipt are dual
    lanes, not doubles).
    """
    from lawvm.sweden.grafter import se_replay_write_receipts
    from lawvm.core.write_receipt import WriteReceipt, receipt_address_string

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om receipt-test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "X", "namnOchEnhet": "X"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Gamla.\n\n2 § Other.\n\n3 § Third.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    before = parse_se_statute(json.dumps(payload).encode("utf-8"))
    # 3 ops: REPLACE §1, INSERT §3a, REPEAL §2
    ops: list[LegalOperation] = [
        LegalOperation(
            op_id="se-replace-1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Ny 1."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="se-insert-3a",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "3a"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="3a", text="Ny 3a."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="se-repeal-2",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "2"),)),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    final, receipts = se_replay_write_receipts(before, ops)

    # All 3 ops applied — 3 receipts emitted.
    assert len(receipts) == 3, [r.op_id for r in receipts]
    for r in receipts:
        assert isinstance(r, WriteReceipt)
    # The final body should have §1 (replaced text), §3 (unchanged),
    # §3a (inserted). §2 (repealed) is removed.
    sections_after = {child.label for child in final.body.children if child.kind is IRNodeKind.SECTION}
    assert sections_after == {"1", "3", "3a"}, sections_after

    # Categorize by op action family
    by_op = {r.op_id: r for r in receipts}

    # REPLACE §1: replaced_paths = (('section','1'),), pre/post non-empty
    r_replace = by_op["se-replace-1"]
    assert r_replace.action == "replace"
    assert r_replace.bound_target_path == (("section", "1"),)
    assert r_replace.replaced_paths == ((("section", "1"),),)
    assert r_replace.created_paths == ()
    assert r_replace.removed_paths == ()
    assert r_replace.landed_primary_path == (("section", "1"),)
    # pre/post hashes non-empty (section exists before AND after)
    pre_values = list(r_replace.pre_hashes.values())
    post_values = list(r_replace.post_hashes.values())
    assert pre_values and pre_values[0], r_replace.pre_hashes
    assert post_values and post_values[0], r_replace.post_hashes
    # The hash changed (replaced)
    assert pre_values[0] != post_values[0]

    # INSERT §3a: created_paths = (('section','3a'),), pre_hash "" (absent
    # before), post_hash non-empty (present after)
    r_insert = by_op["se-insert-3a"]
    assert r_insert.action == "insert"
    assert r_insert.created_paths == ((("section", "3a"),),)
    assert r_insert.replaced_paths == ()
    assert r_insert.removed_paths == ()
    pre_insert = list(r_insert.pre_hashes.values())[0]
    post_insert = list(r_insert.post_hashes.values())[0]
    assert pre_insert == "", r_insert.pre_hashes  # absent before = empty hash
    assert post_insert != "", r_insert.post_hashes  # present after

    # REPEAL §2: removed_paths = (('section','2'),), pre_hash non-empty
    # (present before), post_hash "" (absent after)
    r_repeal = by_op["se-repeal-2"]
    assert r_repeal.action == "repeal"
    assert r_repeal.removed_paths == ((("section", "2"),),)
    assert r_repeal.created_paths == ()
    assert r_repeal.replaced_paths == ()
    pre_repeal = list(r_repeal.pre_hashes.values())[0]
    post_repeal = list(r_repeal.post_hashes.values())[0]
    assert pre_repeal != "", r_repeal.pre_hashes  # present before
    assert post_repeal == "", r_repeal.post_hashes  # absent after

    # The hash key for pre/post is the receipt_address_string of the
    # landed primary path.
    expected_key_replace = receipt_address_string((("section", "1"),))
    assert expected_key_replace in r_replace.pre_hashes
    assert expected_key_replace in r_replace.post_hashes


def test_se_replay_write_receipts_emits_no_receipt_for_skipped_ops() -> None:
    """Skipped ops emit no receipt; the FilterResult rejected_items lane carries them.

    Dual-lane design (§1.8 + §2.3): the FilterResult[LegalOperation]
    ``rejected_items`` lane is the typed-conservation record (what was
    rejected and why); the WriteReceipt lane is the typed-write record
    (what was written and to what covering region). A skipped op is in
    the rejected lane (no write happened); an applied op is in the
    receipt lane (no rejected reason). The two are mutually exclusive per
    op — a non-empty receipt set means the op applied, a non-empty
    rejected_items set means the op was skipped.
    """
    from lawvm.sweden.grafter import se_replay_write_receipts, apply_se_ops_conserved

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om receipt-test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "X", "namnOchEnhet": "X"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § En §.\n\n2 § Annan §.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    before = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        # Successful: REPLACE §1 with valid payload
        LegalOperation(
            op_id="se-replace-1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Ny 1."),
            source=OperationSource(statute_id="2026:999"),
        ),
        # Skipped: REPLACE §9 (target not found)
        LegalOperation(
            op_id="se-replace-9-missing",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="Ny."),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    final, receipts = se_replay_write_receipts(before, ops)
    assert len(receipts) == 1, [r.op_id for r in receipts]
    assert receipts[0].op_id == "se-replace-1"

    # Cross-check: the conserved FilterResult one-receipt-per-applied-op
    # invariant holds -- the rejected lane carries the skipped op.
    conserved = apply_se_ops_conserved(before, ops)
    assert len(conserved.applied_ops) == 1
    assert conserved.applied_ops[0].op_id == "se-replace-1"
    assert len(conserved.skipped_items) == 1
    assert conserved.skipped_items[0].item.op_id == "se-replace-9-missing"

    # Cross-check: the file-state match -- `final` statute (from the per-op
    # receipt-emitting pass) must equal what `apply_se_ops` returns for the
    # full op list (the per-op apply is associative and order-preserving for
    # SE's REPLACE family, which this fixture exercises).
    assert se_statute_from_before_final_match(final, conserved.statute)


def se_statute_from_before_final_match(a: IRStatute, b: IRStatute) -> bool:
    """Helper: compare two IRStatute bodies for full content + order equivalence.

    The earlier version compared only the top-level child ``{(kind, label)}``
    SETS — that would pass even if per-op replay diverged in body TEXT or in
    child ORDER (the "associative and order-preserving" claim was therefore not
    actually exercised). This compares the canonical structural subtree hash of
    each whole body, which is order-sensitive and content-sensitive (text,
    attrs, and the full recursive child structure), so divergence in any of
    those fails the match.
    """
    from lawvm.core.ir_helpers import structural_subtree_hash

    return structural_subtree_hash(a.body) == structural_subtree_hash(b.body)


def test_se_replay_write_receipts_renumber_receipt_is_well_formed() -> None:
    """Regression (§2.3 receipt): a RENUMBER must emit a MEANINGFUL receipt.

    A RENUMBER removes the source section and re-inserts it under the
    destination label — both are parent children-list changes, so the
    identity-pruned diff reports the body-level change as a single empty-path
    tuple ``((),)`` (a tuple holding an empty path, NOT a coordinate).

    Before the fix, the RENUMBER branch did the equivalent of
    ``replaced_paths = changed`` (== ``((),)``) and
    ``landed_primary_path = changed[0]`` (== ``()``). Result: ``replaced_paths``
    carried the bogus empty path, and ``landed_primary_path == ()`` is falsy so
    the ``if landed_primary_path:`` guard skipped the pre/post hashes entirely
    — the receipt was malformed (empty footprint + empty hashes), exactly the
    INSERT/REPEAL empty-diff problem that those branches already special-case.

    The fix mirrors INSERT/REPEAL: the receipt's footprint is the typed
    ``renumbered_paths = ((from, to),)`` pair, ``landed_primary_path`` is the
    destination coordinate (where the section landed), and the pre/post hashes
    resolve against that destination subtree. ``replaced_paths`` no longer
    carries the bogus empty path.
    """
    from lawvm.sweden.grafter import se_replay_write_receipts

    payload = {
        "beteckning": "2026:999",
        "rubrik": "Förordning (2026:999) om renumber-receipt-test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "X", "namnOchEnhet": "X"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Flyttbar text.\n\n5 § Annan.",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    before = parse_se_statute(json.dumps(payload).encode("utf-8"))
    # RENUMBER §2 -> §4 (a non-colliding destination, so the op applies).
    ops = [
        LegalOperation(
            op_id="se-renumber-2-to-4",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "2"),)),
            destination=LegalAddress(path=(("section", "4"),)),
            source=OperationSource(statute_id="2026:999"),
        ),
    ]
    final, receipts = se_replay_write_receipts(before, ops)

    # The renumber applied: §2 gone, §4 (with §2's text) present.
    section_map = se_section_text_map(final)
    assert set(section_map) == {"4", "5"}, section_map
    assert "Flyttbar text" in section_map["4"]

    assert len(receipts) == 1, [r.op_id for r in receipts]
    r = receipts[0]
    assert r.action == "renumber"

    # The footprint is the typed (from_path, to_path) pair — NOT a bogus
    # empty path. renumbered_paths is a tuple of (from, to) leg pairs.
    from_path = (("section", "2"),)
    to_path = (("section", "4"),)
    assert r.renumbered_paths == ((from_path, to_path),)
    # The bug signature: `replaced_paths` must NOT carry the empty-path tuple.
    assert r.replaced_paths == ()
    assert () not in r.replaced_paths
    # Neither renumber leg is the bogus empty path.
    for leg_from, leg_to in r.renumbered_paths:
        assert leg_from != () and leg_to != ()

    # landed_primary_path is the destination coordinate (a real coordinate),
    # not the empty path `()` that the old `changed[0]` produced.
    assert r.landed_primary_path == (("section", "4"),)
    assert r.landed_primary_path  # truthy — the hash guard now fires

    # pre/post hashes are populated (the malformed receipt left these empty).
    # The destination §4 was ABSENT before (pre = "") and PRESENT after.
    assert r.pre_hashes, r.pre_hashes
    assert r.post_hashes, r.post_hashes
    pre = list(r.pre_hashes.values())[0]
    post = list(r.post_hashes.values())[0]
    assert pre == "", r.pre_hashes  # §4 absent before
    assert post != "", r.post_hashes  # §4 present after


def test_check_se_official_replay_emits_renumber_receipt_with_migration_rule_id() -> None:
    """Fire-drill (§2.9 guard-liveness): the SE RENUMBER receipt with the
    ``se_renumber_relabel`` migration_rule_id MUST land on the production apply
    path ``check_se_official_replay`` → ``apply_se_ops_conserved(emit_receipts=True)``
    → ``se_replay_write_receipts`` → ``_se_emit_one_op_receipt``.

    Pre-fix state:
    * The production caller at ``fetch.py:3413`` invoked bare ``apply_se_ops``,
      so the conserved wrapper was exercised only by tests — a §2.9 worst-class
      silent failure (a guard that exists but is unreachable from production).
    * Even when the receipt was constructed in tests, ``migration_rule_ids``
      defaulted to ``()``, so ``WriteReceipt.divergence_explained`` returned
      False and the receipt audited as ``violation`` in
      ``build_observed_write_audit`` (a §1.6 unstated-migration violation that
      strict mode must reject).

    The fix routes production through the conserved wrapper with
    ``emit_receipts=True`` and names the rule ``se_renumber_relabel`` for the
    RENUMBER case so the bound→landed divergence is explained.
    """
    base_payload = {
        "beteckning": "2026:998",
        "rubrik": "Förordning (2026:998) om renumber-fire-drill",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "2 § Flyttbar text.\n\n5 § Annan.\n",
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:999",
        "title": "Förordning om ändring i förordningen (2026:998) om renumber-fire-drill",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:998",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2026:998) om renumber-fire-drill "
            "dels att nuvarande 2 § ska betecknas 4 §, dels att 4 § ska ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "4", "text": "Ny lydelse för §4."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:998/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:999/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:999")

    # The production lane emits typed write receipts via the conserved
    # wrapper's `emit_receipts=True` flag (the §2.9 fix). They land on the
    # result dict's evidence subtree via `_se_write_receipt_to_projection`.
    write_receipts = result["evidence"]["write_receipts"]
    renumber_receipts = [r for r in write_receipts if r["action"] == "renumber"]
    assert len(renumber_receipts) == 1, [r.get("action") for r in write_receipts]
    receipt = renumber_receipts[0]

    # The §4 receipt contract: bound_target_path (source label) diverges from
    # landed_primary_path (destination label) — the divergence MUST be
    # explained by a named migration rule.
    assert receipt["bound_target_path"] == [["section", "2"]]
    assert receipt["landed_primary_path"] == [["section", "4"]]
    # Projection shape: ``renumbered_paths`` is a list of (from_path, to_path)
    # pairs; each path is a list of ``[kind, label]`` step-lists. For the
    # RENUMBER 2 → 4, both legs are single-step section paths.
    assert receipt["renumbered_paths"] == [
        [[["section", "2"]], [["section", "4"]]],
    ]
    assert receipt["migration_rule_ids"] == ["se_renumber_relabel"]
    assert receipt["recovery_rule_ids"] == []
    assert receipt["fallback_rule_ids"] == []
    # bound != landed AND migration_rule_ids non-empty → divergence_explained
    # is True (the §4 receipt-contract property). Projected into the result
    # dict so downstream strict-mode audit consumers can classify the receipt
    # as `qualified` rather than `violation`.
    assert receipt["divergence_explained"] is True

    # The receipt's pre/post hashes resolve at the destination coordinate
    # (where the section landed): §4 was ABSENT before, present after.
    assert list(receipt["pre_hashes"].keys()) == ["section:4"]
    assert receipt["pre_hashes"]["section:4"] == ""
    assert receipt["post_hashes"]["section:4"] != ""

    # Reconstruct the typed WriteReceipt from the projection to confirm the
    # serialized form faithfully represents the typed `divergence_explained`
    # property — the typed object IS the source of truth (per
    # notes/APPLY_RESOLUTION_AND_RECEIPT_CONTRACT.md §4), so round-tripping
    # through the projection proves a future projection-layer rename cannot
    # fake the audit.
    from lawvm.core.write_receipt import WriteReceipt

    def _to_path(path_list: list[list[str]]) -> tuple[tuple[str, str], ...]:
        # Each step is a 2-item ``[kind, label]`` list — unpack explicitly so
        # ``ty`` infers ``tuple[str, str]`` rather than ``tuple[str, ...]``.
        return tuple((step[0], step[1]) for step in path_list)

    def _to_paths(paths_list: list[list[list[str]]]) -> tuple[tuple[tuple[str, str], ...], ...]:
        return tuple(_to_path(p) for p in paths_list)

    def _to_renumbered_paths(
        renumbered_list: list[list[list[list[str]]]],
    ) -> tuple[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]], ...]:
        return tuple(
            (_to_path(from_path), _to_path(to_path))
            for from_path, to_path in renumbered_list
        )

    typed = WriteReceipt(
        op_id=receipt["op_id"],
        helper=receipt["helper"],
        action=receipt["action"],
        bound_target_path=_to_path(receipt["bound_target_path"]),
        landed_primary_path=_to_path(receipt["landed_primary_path"]),
        created_paths=_to_paths(receipt["created_paths"]),
        replaced_paths=_to_paths(receipt["replaced_paths"]),
        removed_paths=_to_paths(receipt["removed_paths"]),
        renumbered_paths=_to_renumbered_paths(receipt["renumbered_paths"]),
        placeholder_created_paths=_to_paths(receipt["placeholder_created_paths"]),
        placeholder_consumed_paths=_to_paths(receipt["placeholder_consumed_paths"]),
        recovery_rule_ids=tuple(receipt["recovery_rule_ids"]),
        migration_rule_ids=tuple(receipt["migration_rule_ids"]),
        fallback_rule_ids=tuple(receipt["fallback_rule_ids"]),
        pre_hashes=dict(receipt["pre_hashes"]),
        post_hashes=dict(receipt["post_hashes"]),
    )
    assert typed.divergence_explained is True
    assert typed.named_rule_ids == ("se_renumber_relabel",)

    # The §1.8 FilterResult landed too — "no unsupported lane disappears":
    # the synthetic statute produces 2 applied ops (the RENUMBER + the REPLACE
    # on §4 by the official act's provision list) and zero rejections.
    apply_filter = result["evidence"]["apply_filter_result"]
    assert apply_filter["accepted_op_count"] == 2
    assert apply_filter["rejected_op_count"] == 0
    assert apply_filter["rejected_reason_codes"] == []


def test_apply_se_ops_records_renumber_and_heading_skip_adjudications() -> None:
    payload = {
        "beteckning": "2026:998",
        "rubrik": "Förordning (2026:998) om test av renummerering",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-12-01T00:00:00",
            "andringInford": None,
            "forfattningstext": ("2 § Ändringsbar text.\n\n3 § Existerande måltext.\n"),
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    statute = parse_se_statute(json.dumps(payload).encode("utf-8"))
    ops = [
        LegalOperation(
            op_id="heading-insert-without-payload",
            sequence=1,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "2"),), special=FacetKind.HEADING),
            payload=None,
            source=OperationSource(statute_id="2026:998"),
        ),
        LegalOperation(
            op_id="renumber-missing-destination",
            sequence=2,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "2"),)),
            destination=None,
            source=OperationSource(statute_id="2026:998"),
        ),
        LegalOperation(
            op_id="renumber-into-existing-section",
            sequence=3,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "2"),)),
            destination=LegalAddress(path=(("section", "3"),)),
            source=OperationSource(statute_id="2026:998"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_se_ops(statute, ops, adjudications_out=adjudications)

    assert [adj.kind for adj in adjudications] == [
        "se_replay_payload_missing",
        "se_replay_destination_missing",
        "se_replay_renumber_collision",
    ]
    assert adjudications[0].detail["target"] == "2"
    assert adjudications[1].detail["action"] == "renumber"
    assert adjudications[2].detail["destination"] == "3"


def test_apply_se_ops_inserts_section_heading_and_appendix_for_mixed_family() -> None:
    payload = {
        "beteckning": "2023:676",
        "rubrik": "Förordning (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "ikraftDateTime": "2023-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2023-06-01T00:00:00",
            "andringInford": "t.o.m. SFS 2026:290",
            "forfattningstext": ("2 § Gammal lydelse.\n\n7 § Befintlig paragraf.\n\n8 § Efterföljande paragraf.\n"),
        },
        "publiceradDateTime": "2023-06-30T00:00:00",
        "andringsforfattningar": [],
    }
    act = {
        "sfs_id": "2026:290",
        "title": "Förordning om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "act_type": "förordning",
        "amended_act_sfs_id": "2023:676",
        "is_amending_act": True,
        "published_date": "2026-03-28",
        "issued_date": "2026-03-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters "
            "tillgänglighet dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, "
            "en ny bilaga, bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 maj 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            {"label": "2", "text": "Ny lydelse."},
            {"label": "7a", "text": "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."},
        ],
        "inserted_headings": [{"before_label": "7a", "text": "Kriterier för funktionsprestanda"}],
        "appendices": [{"label": "3", "title": "", "text": "1. Ett krav."}],
        "signatories": [],
        "footnotes": [],
    }

    statute = parse_se_statute(payload)
    ops = compile_se_official_act_ops(act, source_id="2026:290")
    replayed = apply_se_ops(statute, ops)

    section_map = se_section_text_map(replayed)
    heading_map = se_heading_before_section_map(replayed)
    appendix_map = se_appendix_text_map(replayed)

    assert section_map["2"] == "Ny lydelse."
    assert section_map["7a"] == "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."
    assert heading_map["7a"] == "Kriterier för funktionsprestanda"
    assert "Ett krav." in appendix_map["3"]
    assert replayed.metadata.get("invariant_violations", []) == []


def test_apply_se_ops_handles_renumber_then_replace_and_insert() -> None:
    payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) med instruktion för Socialstyrelsen",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "9 § Gammal 9.\n\n10 § Gammal 10.\n\n17 § Gammal 17.\n\n18 § Gammal 18.\n\n23 § Gammal 23.\n"
            ),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) med instruktion för Socialstyrelsen",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) med instruktion för Socialstyrelsen "
            "dels att nuvarande 17 § ska betecknas 16 §, dels att 9 och 10 §§, den nya 16 §, 18 och 23 §§ "
            "ska ha följande lydelse, dels att det ska införas fem nya paragrafer, 6 a, 17, 18 a, 18 b och 22 §§, "
            "av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [
            {"label": "6a", "text": "Ny 6 a §."},
            {"label": "9", "text": "Ny 9 §."},
            {"label": "10", "text": "Ny 10 §."},
            {"label": "16", "text": "Ny 16 §."},
            {"label": "17", "text": "Ny 17 §."},
            {"label": "18", "text": "Ny 18 §."},
            {"label": "18a", "text": "Ny 18 a §."},
            {"label": "18b", "text": "Ny 18 b §."},
            {"label": "22", "text": "Ny 22 §."},
            {"label": "23", "text": "Ny 23 §."},
        ],
        "signatories": [],
        "footnotes": [],
    }

    statute = parse_se_statute(payload)
    ops = compile_se_official_act_ops(act, source_id="2018:1381")
    ops_json = [se_legal_operation_to_dict(op) for op in ops]
    replayed = apply_se_ops(statute, [se_legal_operation_from_dict(item) for item in ops_json])

    section_map = se_section_text_map(replayed)

    assert set(section_map) == {"6a", "9", "10", "16", "17", "18", "18a", "18b", "22", "23"}
    assert section_map["16"] == "Ny 16 §."
    assert section_map["17"] == "Ny 17 §."
    assert section_map["23"] == "Ny 23 §."
    assert replayed.metadata.get("invariant_violations", []) == []


def test_apply_se_ops_handles_repeal_renumber_replace_insert_family() -> None:
    payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "3 § Gammal 3.\n\n"
                "4 § Gammal 4.\n\n"
                "11 § Gammal 11.\n\n"
                "16 § Gammal 16.\n\n"
                "17 § Gammal 17.\n\n"
                "17 a § Gammal 17 a.\n\n"
                "17 b § Gammal 17 b.\n\n"
                "21 § Gammal 21.\n\n"
                "22 § Gammal 22.\n\n"
                "22 a § Gammal 22 a.\n"
            ),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    act = {
        "sfs_id": "2026:63",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2026-02-10",
        "issued_date": "2026-02-05",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att 16, 17 och 22 §§ ska upphöra att gälla, "
            "dels att nuvarande 17 a, 17 b och 22 a §§ ska betecknas 16, 17 och 22 §§, "
            "dels att 3, 4, 11 och 21 §§ ska ha följande lydelse, "
            "dels att det ska införas två nya paragrafer, 5 a och 15 b §§, av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 mars 2026.",
        "affected_section_labels": ["16", "17", "22"],
        "provisions": [
            {"label": "3", "text": "Ny 3 §."},
            {"label": "4", "text": "Ny 4 §."},
            {"label": "5a", "text": "Ny 5 a §."},
            {"label": "11", "text": "Ny 11 §."},
            {"label": "15b", "text": "Ny 15 b §."},
            {"label": "21", "text": "Ny 21 §."},
        ],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }

    statute = parse_se_statute(payload)
    ops = compile_se_official_act_ops(act, source_id="2026:63")
    replayed = apply_se_ops(statute, ops)
    section_map = se_section_text_map(replayed)

    assert section_map["3"] == "Ny 3 §."
    assert section_map["4"] == "Ny 4 §."
    assert section_map["5a"] == "Ny 5 a §."
    assert section_map["11"] == "Ny 11 §."
    assert section_map["15b"] == "Ny 15 b §."
    assert section_map["16"] == "Gammal 17 a."
    assert section_map["17"] == "Gammal 17 b."
    assert section_map["21"] == "Ny 21 §."
    assert section_map["22"] == "Gammal 22 a."
    assert "17a" not in section_map
    assert "17b" not in section_map
    assert "22a" not in section_map


def test_sweden_legal_operation_roundtrips_typed_text_patch() -> None:
    op = LegalOperation(
        op_id="se-text-patch",
        sequence=1,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", "1"),)),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="typed text", occurrence=3),
            replacement="typed replacement",
        ),
        source=OperationSource(statute_id="2015:284"),
    )

    payload = se_legal_operation_to_dict(op)
    assert "text_match" not in payload
    assert "text_replacement" not in payload
    assert "text_occurrence" not in payload
    assert payload["text_patch"]["selector"]["match_text"] == "typed text"

    replayed = se_legal_operation_from_dict(payload)
    assert replayed.text_patch is not None
    assert replayed.text_patch.selector.match_text == "typed text"
    assert replayed.text_patch.selector.occurrence == 3
    assert replayed.text_patch.replacement == "typed replacement"


def test_se_section_text_map_includes_subsection_item_text() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }

    statute = parse_se_statute(payload)
    section_text = se_section_text_map(statute)["11"]

    assert "Nedan angivna myndigheter ska lämna uppgifter." in section_text
    assert "Polismyndigheten" in section_text


def test_extract_se_current_section_texts_preserves_active_raw_section_text() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-04-15/\nGamla lydelsen.\n\n"
                "2 § /Träder i kraft I:2026-04-15/\nNya lydelsen.\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }

    pre = extract_se_current_section_texts(payload, "2026-04-14")
    post = extract_se_current_section_texts(payload, "2026-04-15")

    assert pre["2"] == "Gamla lydelsen."
    assert post["2"] == "Nya lydelsen."


def test_extract_se_current_section_texts_does_not_split_on_indented_cross_reference() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Beslut enligt 33 kap. 6 eller\n"
                "                                8 § brottsbalken om tillstånd.\n"
                "12 § Nästa paragraf.\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }

    sections = extract_se_current_section_texts(payload, "2026-04-15")

    assert "11" in sections
    assert "8 § brottsbalken om tillstånd." in sections["11"]
    assert sections["12"] == "Nästa paragraf."


def test_extract_se_current_section_texts_stops_before_following_heading() -> None:
    payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "Digital arkivering\n\n"
                "12 § Nästa paragraf.\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }

    sections = extract_se_current_section_texts(payload, "2026-04-15")

    assert sections["11"].endswith("Beslut i nådeärenden.")
    assert "Digital arkivering" not in sections["11"]
    assert sections["12"] == "Nästa paragraf."


def test_canonicalize_se_table_section_text_matches_official_and_current_shapes() -> None:
    official = (
        "Nedan angivna myndigheter ska lämna uppgifter till Kriminalvården i följande fall.\n\n"
        "Uppgift lämnas av\n\n"
        "Uppgift lämnas om\n\n"
        "1. Polismyndigheten\n\n"
        "Beslut i nådeärenden.\n\n"
        "2. Regeringskansliet\n\n"
        "Överklagande av dom eller slutligt beslut."
    )
    current = (
        "Nedan angivna myndigheter ska lämna uppgifter till Kriminalvården i följande fall.\n\n"
        "Uppgift lämnas av\tUppgift lämnas om\n\n"
        "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
        "2. Regeringskansliet\tÖverklagande av dom eller slutligt beslut.\n\n"
        "Förordning (2026:286)."
    )

    assert canonicalize_se_table_section_text(official) == canonicalize_se_table_section_text(current)


def test_check_se_official_replay_matches_table_section() -> None:
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Upphör att gälla U:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "Förordning (2026:286)."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 11 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["11"],
        "provisions": [
            {
                "label": "11",
                "text": (
                    "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                    "Uppgift lämnas av\n\n"
                    "Uppgift lämnas om\n\n"
                    "1. Polismyndigheten\n\n"
                    "Beslut i nådeärenden."
                ),
            }
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["rows"][0]["classification"] == "table_rows_match"


def test_check_se_official_replay_repeal_section_classifies_oracle_stub_as_match() -> None:
    """Editorial repeal-stub vs empty replay is an editorial-stub match, not mismatch.

    The SFS current-text oracle keeps a one-line tombstone "Har upphävts genom
    <förordning|lag> (YEAR:N)." in place of a repealed section, while the
    replay-fold (correctly) produces no section text after a structural
    REPEAL — the section is gone. Classifying this as a ``content_mismatch``
    inflates the genuine-mismatch rate without flagging any replay defect; the
    two surfaces agree on the fact of the repeal.

    Real-corpus witness: SFS 2002:12 §17 (the section was repealed by 2002:12
    itself, and the official consolidation carries the section's title set
    populated with the repeal-stub line). Previously misclassified as
    ``content_mismatch`` (genuine_mismatch bucket); now classified as
    ``repeal_stub_oracle_only`` (genuine_match bucket).

    Regression (synthetic; mirrors the witness shape so it does not depend on
    the archived corpus): the row MUST be match=True, classification MUST be
    ``repeal_stub_oracle_only``, and the row's ``bucket_*`` aggregation MUST
    count it as a genuine_match (not a genuine_mismatch).
    """
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            # The current-text oracle's post-amendment state carries a repeal-stub
            # for §17 (SFS editorial convention: the section's tombstone line).
            "forfattningstext": "17 § Har upphävts genom förordning (2026:286).\n",
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        # Repeal enacting clause — compiles to ``REPEAL section:17`` op, so the
        # replay-fold produces no §17 in the post-materialized tree.
        "enacting_clause": (
            "Regeringen föreskriver att 17 § förordningen (2026:106) om något "
            "skall upphöra att gälla."
        ),
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["17"],
        "provisions": [],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    section_row = result["rows"][0]
    assert section_row["section"] == "17"
    assert section_row["match"] is True
    assert section_row["classification"] == "repeal_stub_oracle_only"
    # The replay produced no text for §17 (the section was structurally repealed)
    # and the oracle's post-state carries the "Har upphävts genom..." tombstone.
    assert (section_row["replay_text"] or "").strip() == ""
    assert "Har upphävts genom förordning (2026:286)" in section_row["post_text"]

    # Aggregated bucket assignments honor the editorial-stub classification:
    # it MUST count toward the genuine_match bucket, not the genuine_mismatch
    # bucket.
    summary = scan_se_official_replay_act(archive, "2026:286")
    assert summary["bucket_genuine_match_count"] == 1
    assert summary["bucket_genuine_mismatch_count"] == 0


def test_check_se_official_replay_repeal_then_later_readded_classifies_as_oracle_version_mismatch() -> None:
    """Repealed-in-this-act + later-reinstated section is oracle_version_mismatch.

    Real-corpus witness: SFS 2001:920 §5 — the amending act structurally
    repeals §5 ("5 § förordningen (...) skall upphöra att gälla vid utgången
    av år 2001."), so the replay-fold produces an empty §5 at the act's
    effective date. The official current-surface oracle carries a §5 with
    non-stub text because a *later* amendment re-introduced the section after
    2001:920 (the post text references "I 4 a kap. förordningen (2007:572)
    om värdepappersmarknaden..." — 2007:572 is strictly later than 2001:920).

    Both surfaces are correct at their different time points: replay
    deterministically reflects the post-2001:920 repeal state; the oracle
    reflects the post-2007:572-reinstatement consolidated state. The replay
    is provably correct (REPEAL op + strictly-later consolidation stamp), so
    the row MUST classify as match=True with the
    ``repeal_then_later_replaced_oracle_only`` shape, NOT ``content_mismatch``
    (genuine_mismatch).

    Regression: a synthetic fixture pinning the shape — an amending act that
    REPEALS §5, a base act whose current-text oracle carries a different
    post-2001:920 §5 body sourced from a strictly-later amendment, and a
    consolidation stamp strictly later than 2001:920's effective date.
    """
    base_payload = {
        "beteckning": "1999:146",
        "rubrik": "Förordning (1999:146) om värdepappersmarknaden",
        "ikraftDateTime": "1999-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Finansdepartementet", "namnOchEnhet": "Finansdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "1999-01-01T00:00:00",
            # Later consolidation -- post-text reflects a strictly-later
            # amendment reintroducing §5 with different content from 2001:920.
            "andringInford": "t.o.m. SFS 2007:572",
            "forfattningstext": (
                "5 § /Upphör att gälla U:2001-12-31/\n"
                "Gammal §5 lydelse före 2001:920.\n\n"
                # Later amendment reintroduced §5 with different content
                # observed by 2007:572 consolidation. The post-stamp names
                # a strictly later amendment (2007 > 2001).
                "5 § /Träder i kraft I:2007-XX-XX/\n"
                "I 4 a kap. förordningen (2007:572) om värdepappersmarknaden finns bestämmelser."
            ),
        },
        "publiceradDateTime": "2001-12-31T12:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2001:920",
        "title": "Förordning om ändring i förordningen (1999:146) om värdepappersmarknaden",
        "act_type": "förordning",
        "amended_act_sfs_id": "1999:146",
        "is_amending_act": True,
        "published_date": "2001-11-30",
        "issued_date": "2001-11-15",
        # Repeal-only enacting clause -- compiles to ``REPEAL section:5`` op,
        # so the replay-fold produces no §5 in the materialized post-tree.
        "enacting_clause": (
            "Regeringen föreskriver att 5 § förordningen (1999:146) om "
            "värdepappersmarknaden skall upphöra att gälla vid utgången av år 2001."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 januari 2002.",
        "affected_section_labels": ["5"],
        "provisions": [],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/1999:146/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2001:920/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2001:920")

    assert result["match_count"] == 1, [r for r in result["rows"]]
    section_row = result["rows"][0]
    assert section_row["section"] == "5"
    # The replay produced no text for §5 (structurally repealed) -- the
    # classification MUST NOT be ``content_mismatch`` (the previous behavior,
    # when the comparator diffed the empty replay against the later-oracle's
    # readded §5 text).
    assert section_row["match"] is True
    assert section_row["classification"] == "repeal_then_later_replaced_oracle_only"

    # Aggregated bucket: the row MUST count toward the oracle_version_mismatch
    # bucket (correct replay measured against a later consolidation), NOT the
    # genuine_mismatch bucket.
    summary = scan_se_official_replay_act(archive, "2001:920")
    assert summary["bucket_oracle_version_mismatch_count"] == 1, summary
    assert summary["bucket_genuine_mismatch_count"] == 0, summary


def test_check_se_official_replay_repeal_stub_pattern_is_narrow() -> None:
    """The repeal-stub matcher MUST NOT fire on adjacent-but-different phrasings.

    The classification is shape-specific: only the bare "Har upphävts genom
    <förordning|lag> (YEAR:N)." tombstone matches. A neighboring phrasing that
    mentions a repeal but carries additional sentence content (`Har upphävts
    i sin helhet genom förordning (2026:286).` or a sentence describing the
    repeal as part of a broader paragraph) does NOT match — those remain
    content_mismatch candidates for the regular classifier to handle.
    """
    assert _is_oracle_repeal_stub("Har upphävts genom förordning (2026:286).") is True
    assert _is_oracle_repeal_stub("Har upphävts genom lag (1999:353).") is True
    assert _is_oracle_repeal_stub("Har upphävts genom förordning (2026:286)") is True  # tolerant of missing trailing period
    # Phrasings that mention a repeal but are NOT the bare tombstone convention.
    assert _is_oracle_repeal_stub("Har upphävts i sin helhet genom förordning (2026:286).") is False
    assert _is_oracle_repeal_stub("Avsd Kristen paragrafen har upphävts genom förordning (2026:286).") is False
    assert _is_oracle_repeal_stub("") is False
    assert _is_oracle_repeal_stub("Vanlig lydelse som inte är en upphävst stub.") is False


def test_check_se_official_replay_regenerates_cached_ops_with_truncated_payload_text() -> None:
    """Stale cached ops with truncated REPLACE payloads MUST force a recompile.

    Real-corpus witness: SFS 2001:606 §72 — the cached ``official.ops.json`` was
    built before the parser learned to fold wrapped cross-reference
    continuations back into their host section. The cached §72 REPLACE op's
    payload carried only the truncated provision body (``child_text_len=148``,
    the snapshot stopped mid-sentence at ``...avses in``) while the runtime-
    coerced official act's §72 provision now carries the full text
    (``len=995``, including the folded wrap-continuation ``första stycket och
    67 §. Införingen av uppgifter...``).

    Without a recompile trigger the replay-vs-oracle lookup returned the
    truncated text and the row was misclassified as ``content_mismatch``
    (genuine_mismatch bucket). After the staleness fix, the cached-ops
    material-text shortfall against the coerced provision fires an in-memory
    recompile; the fresh compile uses the coerced act's folded provisions and
    produces the full-body §72 payload; the replay-vs-oracle lookup now
    matches the post-state via the official-act oracle and classifies the row
    as ``official_oracle_version_mismatch`` match=True.

    Regression (synthetic; mirrors the witness shape). The fixture carries a
    §72 REPLACE op with a deliberately truncated payload; the
    ``apply_se_states``-equivalent runtime path used by ``check_se_official_replay``
    MUST detect the staleness, force a fresh in-memory compile against the
    coerced act's full-body §72 text, and apply the full payload.
    """
    full_body = (
        "Sedan en underrättelse som avses i 74 § har kommit in, skall "
        "Lantmäteriverket snarast möjligt i fastighetsregistret föra in de "
        "uppgifter som avses i första stycket och 67 §. Införingen av "
        "uppgifter från beskattningsdatabasen skall med beaktande av 64 § "
        "andra stycket ske senast i samband med årsskifte."
    )
    truncated_body = (
        "Sedan en underrättelse som avses i 74 § har kommit in, skall "
        "Lantmäteriverket snarast möjligt i fastighetsregistret föra in de "
        "uppgifter som avses in"
    )
    base_payload = {
        "beteckning": "2000:308",
        "rubrik": "Förordning (2000:308) om fastighetsregister",
        "ikraftDateTime": "2000-05-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Finansdepartementet", "namnOchEnhet": "Finansdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2001-08-16T00:00:00",
            # Later-consolidation stamp so the replay-vs-cached-official
            # fallback path can fire after the staleness-triggered recompile.
            "andringInford": "t.o.m. SFS 2003:500",
            "forfattningstext": (
                # §72 was repealed-and-replaced at 2001:606's effective date
                # and stayed in the same shape through 2003:500.
                "72 § /Upphör att gälla U:2001-10-01/\n"
                f"{truncated_body}\n\n"
                "72 § /Träder i kraft I:2001-10-01/\n"
                f"{full_body}\n"
                "Förordning (2001:606)."
            ),
        },
        "publiceradDateTime": "2001-08-31T12:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2001:606",
        "title": "Förordning om ändring i förordningen (2000:308) om fastighetsregister",
        "act_type": "förordning",
        "amended_act_sfs_id": "2000:308",
        "is_amending_act": True,
        "published_date": "2001-09-01",
        "issued_date": "2001-08-16",
        "enacting_clause": (
            "Regeringen föreskriver att 72 § förordningen (2000:308) om "
            "fastighetsregister skall ha följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 oktober 2001.",
        "affected_section_labels": ["72"],
        # The coerced-provision §72 text carries the FULL body — the
        # runtime coercion folds the wrap-continuation back into §72's text.
        "provisions": [{"label": "72", "text": full_body}],
        "inserted_headings": [],
        "appendices": [],
        "signatories": [],
        "footnotes": [],
    }
    # Stale cached ops built before the parser fix: the cached §72 REPLACE
    # op's payload carries the truncated body the pre-fix parser left at the
    # wrap break (the rest folded away into a duplicate-label ghost).
    stale_cached_op = {
        "op_id": "se_official_2001:606_72",
        "sequence": 1,
        "action": "replace",
        "target": {"path": [["section", "72"]], "special": None},
        "targets": [{"path": [["section", "72"]], "special": None}],
        "payload": {
            "kind": "section",
            "label": "72",
            "text": "",
            "attrs": {},
            # Truncated to the half before the wrap; the cross-reference
            # continuation was never folded into this cached payload.
            "children": [{"kind": "subsection", "label": "1", "text": truncated_body, "attrs": {}, "children": []}],
        },
        "anchor": None,
        "destination": None,
        "source": {
            "statute_id": "2001:606",
            "title": official_act["title"],
            "enacted": "2001-08-16",
            "effective": "2001-10-01",
            "expires": "",
            "raw_text": official_act["enacting_clause"],
            "corrected_by": "",
            "commencement_source": "",
            "commencement_title": "",
        },
        "applicability": [],
        "provenance_tags": [],
        "text_match": None,
        "text_replacement": None,
        "text_occurrence": 0,
        "group_id": None,
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2000:308/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2001:606/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2001:606/official.ops.json": json.dumps([stale_cached_op], ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2001:606")

    # The §72 row MUST now match: the cached ops' truncated-payload staleness
    # was detected and a fresh in-memory compile filled the §72 replacement
    # with the full body. After applying the fresh REPLACE op, the replayed
    # §72 text equals the official-act oracle (the cached raw provision's
    # full body), and the current-surface disagreement is a strictly later
    # consolidation (oracle_version_mismatch), NOT a ``content_mismatch``
    # (the pre-fix behavior where the truncated replay was diffed against
    # the full oracle text).
    assert result["match_count"] == 1
    section_row = result["rows"][0]
    assert section_row["section"] == "72"
    assert section_row["match"] is True, section_row
    assert section_row["classification"] != "content_mismatch", section_row["classification"]
    # And the replayed §72 text now contains the wrap-continuation fragment,
    # proving the cached truncated-payload was replaced by the fresh full-body
    # compile rather than silently reused.
    assert "första stycket och 67 §" in section_row["replay_text"]
    assert "Införingen av uppgifter" in section_row["replay_text"]


def test_check_se_official_replay_regenerates_cached_ops_with_duplicate_target() -> None:
    """Stale cached ops with a duplicate (kind, label) REPLACE MUST force a recompile.

    Real-corpus witness: SFS 2001:606 — the cached ``official.ops.json`` was
    built before the parser learned to fold duplicate-label wrapped cross-
    reference continuations back into their host section. The cached ops file
    contained TWO REPLACE §64 ops (one for the legitimate §64 text, one for
    the wrap-continuation ghost whose label collided with §64). The current
    compiler no longer emits duplicate-target REPLACE ops — the runtime
    coercion folds the wrap-continuation silently — so a cached duplicate
    is a strong staleness signal.

    Regression (synthetic; mirrors the witness shape). The fixture carries
    two cached REPLACE §1 ops; re-running ``check_se_official_replay``
    forces a fresh compile that emits a single REPLACE §1 op whose payload
    carries the legitimate provision body.
    """
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": "t.o.m. SFS 2027:999",  # later consolidation -> version_mismatch
            "forfattningstext": (
                "1 § /Upphör att gälla U:2026-04-15/\n"
                "Gammal lydelse.\n\n"
                "1 § /Träder i kraft I:2026-04-15/\n"
                "Ny lydelse för §1."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    new_section_text = "Ny lydelse för §1."
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 1 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["1"],
        "provisions": [{"label": "1", "text": new_section_text}],
        "signatories": [],
        "footnotes": [],
    }
    # TWO cached REPLACE §1 ops — pre-fix compiler emitted one per cached
    # duplicate-label provision. Both carry the same payload text because
    # the pre-fix lower function did a `next()` lookup-by-label that always
    # returned the first matching cached provision.
    def cached_op(seq: int) -> dict:
        return {
            "op_id": f"se_official_replace_2026:286_1_{seq}",
            "sequence": seq,
            "action": "replace",
            "target": {"path": [["section", "1"]], "special": None},
            "targets": [{"path": [["section", "1"]], "special": None}],
            "payload": {
                "kind": "section",
                "label": "1",
                "text": "",
                "attrs": {},
                "children": [{"kind": "subsection", "label": "1", "text": new_section_text, "attrs": {}, "children": []}],
            },
            "anchor": None,
            "destination": None,
            "source": {
                "statute_id": "2026:286",
                "title": official_act["title"],
                "enacted": "2026-03-19",
                "effective": "2026-04-15",
                "expires": "",
                "raw_text": official_act["enacting_clause"],
                "corrected_by": "",
                "commencement_source": "",
                "commencement_title": "",
            },
            "applicability": [],
            "provenance_tags": [],
            "text_match": None,
            "text_replacement": None,
            "text_occurrence": 0,
            "group_id": None,
        }

    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.ops.json": json.dumps([cached_op(1), cached_op(2)], ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    # The duplicate-target cached ops triggered a fresh in-memory recompile;
    # the replay produced exactly one REPLACE §1 op row, the material §1
    # text matches the official-provision oracle (modulo whatever editorial
    # presentation drift the regular classifier falls back to), and NO row
    # carries a stale-cache-only artifact. Pre-fix behavior was two §1 rows
    # (one per duplicate cached op); post-fix the duplicate is folded
    # silently at the source, surfaced as a single cohesive replay.
    section_rows = [row for row in result["rows"] if row.get("section") == "1"]
    assert len(section_rows) == 1, section_rows
    assert section_rows[0]["match"] is True, section_rows[0]
    assert section_rows[0]["classification"] != "content_mismatch", section_rows[0]


def test_check_se_official_replay_collects_skipped_replay_ops_as_adjudications() -> None:
    base_payload = {
        "beteckning": "2026:777",
        "rubrik": "Förordning (2026:777) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": (
            "2 § /Upphör att gälla U:2026-04-15/\n"
            "Gammal lydelse.\n\n"
            "2 § /Träder i kraft I:2026-04-15/\n"
            "Ny lydelse. Förordning (2026:286).\n"
        ),
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:777) om test",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:777",
        "is_amending_act": True,
        "published_date": "2026-04-20",
        "issued_date": "2026-04-18",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:777) om test ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "2", "text": "Ny lydelse."}],
        "signatories": [],
        "footnotes": [],
    }
    valid_op = LegalOperation(
        op_id="se_official_replace_2",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny lydelse."),
        source=OperationSource(statute_id="2026:286", effective="2026-04-15"),
    )
    unsupported_op = LegalOperation(
        op_id="unsupported-article",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("article", "10"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="10", text="Ingen effekt."),
        source=OperationSource(statute_id="2026:286", effective="2026-04-15"),
    )
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:777/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.ops.json": json.dumps(
                [se_legal_operation_to_dict(op) for op in [valid_op, unsupported_op]],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["target_count"] == 1
    assert result["match_count"] == 1
    assert len(result["adjudications"]) == 1
    assert result["adjudications"][0]["kind"] == "se_replay_unsupported_target_kind"
    assert result["adjudications"][0]["op_id"] == "unsupported-article"
    assert result["adjudications"][0]["detail"]["action"] == "replace"
    evidence_row = result["evidence"]["finding_rows"][0]
    assert evidence_row["frontend_id"] == "sweden"
    assert evidence_row["family"] == "se_replay_unsupported_target_kind"
    assert evidence_row["rule_id"] == "se_replay_unsupported_target_kind"
    assert evidence_row["phase"] == "replay"
    assert evidence_row["source_artifact_id"] == "2026:286"
    assert evidence_row["source_unit_id"] == "unsupported-article"
    assert evidence_row["strict_disposition"] == "block"
    assert evidence_row["quirks_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(evidence_row) == ()


def _build_se_oracle_drift_archive(andring_inford: str | None) -> _FakeArchive:
    """Archive whose current surface diverges from the amending act's post-state.

    The replay reproduces the amendment's own post-state (== the official-act
    oracle), but the current surface carries a different "Senare driftad lydelse"
    body. ``andring_inford`` sets the current surface's consolidation stamp so the
    oracle-version relation can be exercised.
    """
    base_payload = {
        "beteckning": "2026:777",
        "rubrik": "Förordning (2026:777) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "andringInford": andring_inford,
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-04-15/\n"
                "Gammal lydelse.\n\n"
                "2 § /Träder i kraft I:2026-04-15/\n"
                "Senare driftad lydelse.\n"
            ),
        },
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:777) om test",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:777",
        "is_amending_act": True,
        "published_date": "2026-04-20",
        "issued_date": "2026-04-18",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:777) om test ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "2", "text": "Ny lydelse."}],
        "signatories": [],
        "footnotes": [],
    }
    return _FakeArchive(
        stored={
            "se://sfs/2026:777/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )


def test_check_se_official_replay_later_stamp_is_oracle_version_mismatch() -> None:
    # The current surface's consolidation stamp folds an SFS (2030:1) strictly
    # later than the replayed amendment (2026:286): a correct replay measured
    # against a later consolidation, not a content failure.
    archive = _build_se_oracle_drift_archive("t.o.m. SFS 2030:1")

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["oracle_version_relation"] == "later"
    assert result["oracle_consolidation_sfs_id"] == "2030:1"
    assert result["rows"][0]["classification"] == "official_oracle_version_mismatch"


def test_check_se_official_replay_earlier_stamp_is_surface_drift() -> None:
    # The current surface's stamp is contemporaneous-or-older than the replayed
    # amendment, so an oracle disagreement is a genuine surface drift.
    archive = _build_se_oracle_drift_archive("t.o.m. SFS 2026:200")

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["oracle_version_relation"] == "same_or_earlier"
    assert result["rows"][0]["classification"] == "official_oracle_match_current_surface_drift"


def test_check_se_official_replay_missing_stamp_is_version_unknown() -> None:
    # Without a parseable consolidation stamp the version relation cannot be
    # trusted, so the row is classified honestly as version-unknown rather than
    # guessed into either drift or version-mismatch.
    archive = _build_se_oracle_drift_archive(None)

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["oracle_version_relation"] == "unknown"
    assert result["rows"][0]["classification"] == "official_oracle_match_version_unknown"


def test_check_se_official_replay_successful_path_carries_typed_replay_feasible_outcome() -> None:
    """Structured outcome field on the successful path (no exception-driven control flow).

    The previous behavior raised ``NotImplementedError`` for the
    contamination-older-base path; this test pins the new contract: when
    replay succeeds, the result dict carries ``outcome == "replay_feasible"``
    alongside the existing fields. Callers dispatch on the typed ``outcome``
    field rather than catching exceptions / substring-matching messages.
    """
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Upphör att gälla U:2026-04-15/\n"
                "Gammal 11 §.\n\n"
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Ny 11 §.\n"
                "Förordning (2026:286)."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 11 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["11"],
        "provisions": [{"label": "11", "text": "Ny 11 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["outcome"] == "replay_feasible"
    assert result["match_count"] == 1
    # The existing successful-path fields all survive.
    assert "rows" in result
    assert "target_count" in result
    assert "recovery_mode" in result

    # Scan-summary propagation: the typed outcome propagates to
    # :func:`scan_se_official_replay_act` as the legacy top-level ``outcome``
    # for aggregate-compat, AND the structured fields are not present on the
    # successful path (outcome-only signal is enough when replay succeeds).
    summary = scan_se_official_replay_act(archive, "2026:286")
    assert summary["outcome"] == "replay_ok"
    assert "typed_outcome" not in summary  # no extra structured fields when feasible


def test_check_se_official_replay_recompiles_stale_ops_without_effective_date() -> None:
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Upphör att gälla U:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "Uppgift lämnas av\tUppgift lämnas om\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "Förordning (2026:286)."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 11 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["11"],
        "provisions": [
            {
                "label": "11",
                "text": (
                    "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                    "Uppgift lämnas av\n\n"
                    "Uppgift lämnas om\n\n"
                    "1. Polismyndigheten\n\n"
                    "Beslut i nådeärenden."
                ),
            }
        ],
        "signatories": [],
        "footnotes": [],
    }
    stale_ops = [
        {
            "op_id": "stale",
            "sequence": 1,
            "action": "replace",
            "target": {"path": [["section", "11"]], "special": None},
            "targets": [{"path": [["section", "11"]], "special": None}],
            "payload": {
                "kind": "section",
                "label": "11",
                "text": "",
                "attrs": {},
                "children": [{"kind": "subsection", "label": "1", "text": "stale", "attrs": {}, "children": []}],
            },
            "anchor": None,
            "destination": None,
            "source": {
                "statute_id": "2026:286",
                "title": official_act["title"],
                "enacted": "2026-03-19",
                "effective": "",
                "expires": "",
                "raw_text": official_act["enacting_clause"],
                "corrected_by": "",
                "commencement_source": "",
                "commencement_title": "",
            },
            "applicability": [],
            "provenance_tags": [],
            "text_match": None,
            "text_replacement": None,
            "text_occurrence": 0,
            "group_id": None,
        }
    ]
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.ops.json": json.dumps(stale_ops, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    refreshed_ops = json.loads(archive.stored["se://sfs/2026:286/official.ops.json"].decode("utf-8"))
    assert refreshed_ops[0]["source"]["effective"] == "2026-04-15"


def test_analyze_se_official_replay_feasibility_compiles_in_memory_on_readonly_archive() -> None:
    """Regression — coverage-scan worker previously crashed writing surfaces.

    The scan worker opens the shared ``sweden.farchive`` readonly (Farchive
    default). The analyze entry point used to call
    :func:`compile_se_official_ops_to_archive`, which writes the typed waists
    (clause/payload/elaboration/effects-plan/ops/adjudications) — a write that
    fails with ``sqlite3.OperationalError: attempt to write a readonly
    database`` on every cache-miss scan. With no cached ops in the readonly
    archive, an entire scan corpus would surface nothing but that error and a
    single ``aggregate_se_official_coverage`` row.

    The analyze path now (a) probes writability via ``_se_archive_is_writable``
    and (b) when the archive is read-only, compiles ops in memory using the
    existing ``compile_se_official_act_ops`` pure path instead of attempting
    the mutating cache-refresh. This test pins the new contract against any
    future regression: the analyze path MUST drive a readonly archive without
    crashing, MUST still return a non-empty ops set, and MUST NOT attempt to
    persist any cache rows that would fail.
    """
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "11 § /Upphör att gälla U:2026-04-14/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "11 § /Träder i kraft I:2026-04-15/\n"
                "Nedan angivna myndigheter ska lämna uppgifter.\n\n"
                "1. Polismyndigheten\tBeslut i nådeärenden.\n\n"
                "Förordning (2026:286)."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 11 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["11"],
        "provisions": [
            {
                "label": "11",
                "text": "Nedan angivna myndigheter ska lämna uppgifter.\n\n1. Polismyndigheten\n\nBeslut i nådeärenden.",
            }
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _ReadonlyFakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            # No cached ops/adjudications/surfaces: the readonly path MUST be
            # able to compile without reading a pre-existing cache.
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2026:286")

    # The cache-miss was bridged in memory: ops were computed from the official
    # act surface even though no cache entry existed.
    assert result["op_count"] == 1
    assert isinstance(result["ops_json"], list)
    assert result["ops_json"][0]["target"]["path"][0] == ["section", "11"]
    # The persist side-effect must NOT fire on a readonly archive: no
    # attempted writes (the prior failure mode of crashing coverage-scan
    # workers). This signature also ratifies the writability probe is read
    # before any store(): the exception was raised from inside the store call.
    assert archive.attempted_writes == []
    # And the higher-level entry that escalates the readonly scan picks up the
    # in-memory ops set deterministically through the analysis dict (not by
    # re-loading from the empty cache). The fixture's base carries the
    # post-amendment text already, so the act falls into the
    # older_base_required lane rather than replay_ok — the regression point is
    # that the readonly scan returns a classified outcome instead of crashing
    # on the first cache-miss `archive.store` call, not that this specific
    # fixture produces a replay match.
    scan_summary = scan_se_official_replay_act(archive, "2026:286")
    assert scan_summary["outcome"] != "error"
    assert scan_summary["outcome"] in {"replay_ok", "older_base_required"}


def test_check_se_official_replay_matches_inline_numbering_only_difference() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "18 § /Upphör att gälla U:2026-04-15/\n"
                "Inom myndigheten finns ett särskilt beslutsorgan. Beslutsorganet ska avgöra gamla ärenden.\n\n"
                "18 § /Träder i kraft I:2026-04-15/\n"
                "Inom myndigheten finns ett särskilt beslutsorgan. Beslutsorganet ska avgöra rättsmedicinska ärenden, "
                "ärenden om tillstånd till sterilisering enligt steriliseringslagen (1975:580), och andra ärenden. "
                "Förordning (2026:280)."
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:280",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 18 § förordningen (2015:284) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["18"],
        "provisions": [
            {
                "label": "18",
                "text": (
                    "1 Inom myndigheten finns ett särskilt beslutsorgan. Beslutsorganet ska avgöra "
                    "1. rättsmedicinska ärenden, 2. ärenden om tillstånd till sterilisering enligt "
                    "steriliseringslagen (1975:580), och 3. andra ärenden."
                ),
            }
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:280/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:280")

    assert result["match_count"] == 1
    assert result["rows"][0]["classification"] == "inline_numbering_only"


def test_check_se_official_replay_matches_inline_numbering_before_capital_letter() -> None:
    """Inline list markers before capital-letter body text MUST normalize.

    Real-corpus witness: SFS 1999:1134 (2001:1004 amended by it) §2 carries an
    enumerated list "...Väg 1. En sådan väg, gata, torg..." whose body begins
    with a capital letter. The replay IR-walk renderer (``se_section_text_map``)
    parses the provision body into IR ITEM children whose ``.text`` drops the
    leading ``1.`` enumerator prefix. The cached official-act ``provisions`` raw
    text (from ``parse_se_official_act_text``) keeps that prefix verbatim
    because it is the raw provision text. The existing
    ``se_compare_inline_list_numbering`` normalization rule matched enumerators
    only before lowercase body text. Markers followed by capital-letter body
    fell through, so the replay-vs-cached-official fallback classified the row
    as ``content_mismatch`` even when the replayed body matched the post-stock
    consolidation exactly (real witness: 2001:1004 §2 was ``content_mismatch``
    pre-fit, now ``official_oracle_version_mismatch`` match=True).

    Regression: the normalization MUST now accept capital-letter body too, so
    a section body containing inline ``<N>. <Capital>`` enumerators matches the
    cached official text.
    """
    from lawvm.sweden.fetch import _normalize_compare_text

    # The comparison-time normalization pairs the cached provision text
    # (markers preserved as plain text) with the replay-rendered text (markers
    # stripped). Both should normalize to the same canonical form.
    cached_official = "Väg 1. En sådan väg, gata, torg och annan allmän plats."
    replay_rendered = "Väg En sådan väg, gata, torg och annan allmän plats."
    assert _normalize_compare_text(cached_official) == _normalize_compare_text(replay_rendered), (
        "the replayed text (markers stripped) MUST equal the cached official text "
        "(markers preserved) after the inline-list-numbering normalization fires "
        "for capital-letter body"
    )

    # And the broader end-to-end replay-vs-cached-oracle check matches the
    # capital-letter enumerator case (no trailing attribution so the
    # ``editorial_attribution_only`` classifier branch does not steal the row).
    archive = _FakeArchive(
        stored={
            "se://sfs/1999:1134/rk.current.json": json.dumps(
                {
                    "beteckning": "1999:1134",
                    "rubrik": "Förordning (1999:1134) om belastningsregister",
                    "ikraftDateTime": "1999-01-01T00:00:00",
                    "ikraftOvergangsbestammelse": False,
                    "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
                    "forfattningstypNamn": "Förordning",
                    "register": {"forarbeten": None},
                    "fulltext": {
                        "utfardadDateTime": "1999-01-01T00:00:00",
                        "andringInford": "t.o.m. SFS 2003:500",  # later consolidation -> version_mismatch
                        "forfattningstext": (
                            "2 § /Upphör att gälla U:2002-01-01/\n"
                            "Gammal lydelse.\n\n"
                            "2 § /Träder i kraft I:2002-01-01/\n"
                            "Väg 1. En sådan väg, gata, torg och annan allmän plats."
                        ),
                    },
                    "publiceradDateTime": "2001-12-31T12:00:00",
                    "andringsforfattningar": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            "se://sfs/2001:1004/official.act.json": json.dumps(
                {
                    "sfs_id": "2001:1004",
                    "title": "Förordning om ändring i förordningen (1999:1134) om belastningsregister",
                    "act_type": "förordning",
                    "amended_act_sfs_id": "1999:1134",
                    "is_amending_act": True,
                    "published_date": "2001-09-01",
                    "issued_date": "2001-08-15",
                    "enacting_clause": "Regeringen föreskriver att 2 § förordningen (1999:1134) om belastningsregister skall ha följande lydelse.",
                    "effective_clause": "Denna förordning träder i kraft den 1 januari 2002.",
                    "affected_section_labels": ["2"],
                    "provisions": [
                        {
                            "label": "2",
                            "text": (
                                "Väg 1. En sådan väg, gata, torg och annan allmän plats."
                            ),
                        }
                    ],
                    "inserted_headings": [],
                    "appendices": [],
                    "signatories": [],
                    "footnotes": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2001:1004")

    assert result["match_count"] == 1
    section_row = result["rows"][0]
    assert section_row["section"] == "2"
    # The replay reproduced the §2 replace body and that state equals the
    # cached official text (modulo the inline-list-numbering normalization).
    # The current surface carries a strictly later consolidation stamp
    # ("t.o.m. SFS 2003:500") -> ``official_oracle_version_mismatch`` match=True,
    # NOT a ``content_mismatch``.
    assert section_row["match"] is True
    # After normalization both surfaces match exactly -- ``exact`` is the strongest
    # match classification. The key assertion: this row is NOT a ``content_mismatch``
    # (the bug that previously fired when inline ``<N>. <Capital>`` enumerators
    # were preserved in cached text but stripped in replay text).
    assert section_row["classification"] != "content_mismatch", section_row["classification"]


def test_check_se_official_replay_matches_mixed_section_heading_and_appendix_family() -> None:
    # This fixture intentionally exercises a preexisting insert-target mismatch.
    # Under the strict replay contract, that is a hard precondition block rather
    # than something to recover through compatibility behavior.
    base_payload = {
        "beteckning": "2023:676",
        "rubrik": "Förordning (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "ikraftDateTime": "2023-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2023-06-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-05-01/\nGammal lydelse.\n\n"
                "2 § /Träder i kraft I:2026-05-01/\nNy lydelse. Förordning (2026:290).\n\n"
                "7 § Befintlig paragraf.\n\n"
                "Kriterier för funktionsprestanda /Träder i kraft I:2026-05-01/\n\n"
                "7 a § /Träder i kraft I:2026-05-01/\nDe kriterier för funktionsprestanda som anges i bilaga 3 ska användas. Förordning (2026:290).\n\n"
                "8 § Efterföljande paragraf.\n\n"
                "Bilaga 3 /Träder i kraft I:2026-05-01/\nKriterier för funktionsprestanda\n\n"
                "1. Ett krav.\n"
            ),
        },
        "publiceradDateTime": "2023-06-30T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:290",
        "title": "Förordning om ändring i förordningen (2023:676) om vissa produkters och tjänsters tillgänglighet",
        "act_type": "förordning",
        "amended_act_sfs_id": "2023:676",
        "is_amending_act": True,
        "published_date": "2026-03-28",
        "issued_date": "2026-03-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2023:676) om vissa produkters och tjänsters "
            "tillgänglighet dels att 2 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 7 a §, "
            "en ny bilaga, bilaga 3, och närmast före 7 a § en ny rubrik av följande lydelse."
        ),
        "effective_clause": "Denna förordning träder i kraft den 1 maj 2026.",
        "affected_section_labels": ["2"],
        "provisions": [
            {"label": "2", "text": "Ny lydelse."},
            {"label": "7a", "text": "De kriterier för funktionsprestanda som anges i bilaga 3 ska användas."},
        ],
        "inserted_headings": [{"before_label": "7a", "text": "Kriterier för funktionsprestanda"}],
        "appendices": [{"label": "3", "title": "Kriterier för funktionsprestanda", "text": "1. Ett krav."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2023:676/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:290/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    try:
        result = check_se_official_replay(archive, "2026:290")
    except NotImplementedError:
        raise AssertionError(
            "check_se_official_replay must surface the unresolved replay state as a typed "
            "outcome, not raise NotImplementedError (the exception-driven control flow was "
            "retired; the structured outcome field now carries the typed signal)."
        )

    # The structured outcome surfaces the unresolved replay state without
    # raising: ``outcome`` is one of ``older_base_required`` /
    # ``precondition_issues_blocking`` (both are non-fatal replay-frontier
    # states), and the typed reason_code names the specific unresolved
    # shape. This fixture triggers the precondition_issues_blocking path
    # (the recovered pre_statute lacks structural targets the ops require).
    # The previous NotImplementedError string now lives in the structured
    # ``message`` / ``outcome_detail`` fields so callers can read the typed
    # signal without catching exceptions + substring-matching.
    assert result["outcome"] in {
        "older_base_required",
        "precondition_issues_blocking",
    }
    assert "section:7a:preexisting_insert_target" in result["message"]


def test_check_se_official_replay_reports_current_surface_contamination_for_old_insert_family() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "16 § Preexisting section 16.\n\n17 § Preexisting section 17.\n\n18 a § Preexisting inserted section.\n"
            ),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något dels att nuvarande 17 § ska betecknas 16 §, "
            "dels att den nya 16 § ska ha följande lydelse, dels att det ska införas två nya paragrafer, 17 och 18 a §§, "
            "av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [
            {"label": "16", "text": "Ny 16 §."},
            {"label": "17", "text": "Ny 17 §."},
            {"label": "18a", "text": "Ny 18 a §."},
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    try:
        result = check_se_official_replay(archive, "2018:1381", as_of="2018-08-01")
    except NotImplementedError:
        raise AssertionError(
            "check_se_official_replay must surface older_base_required as a typed outcome, "
            "not raise NotImplementedError (the exception-driven control flow was retired; "
            "the structured outcome field now carries the typed signal)."
        )

    # Structured-typed-outcome surface (replacing the previous
    # NotImplementedError raise): the contamination-not-recoverable path
    # returns outcome=older_base_required + reason_code.
    assert result["outcome"] == "older_base_required"
    assert result["reason_code"] == "se_replay_base_surface_contains_post_amendment_targets"
    assert "historical replay requires an older base surface or reverse patching" in result["message"]
    assert "section:16:preexisting_renumber_destination" in result["message"]
    assert "section:17:preexisting_insert_target" in result["message"]
    assert "section:18a:preexisting_insert_target" in result["message"]


def test_analyze_se_official_replay_feasibility_reports_contamination() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": ("16 § Preexisting section 16.\n\n17 § Preexisting section 17.\n"),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något dels att nuvarande 17 § ska betecknas 16 §, "
            "dels att den nya 16 § ska ha följande lydelse, dels att det ska införas en ny paragraf, 17 §, av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [
            {"label": "16", "text": "Ny 16 §."},
            {"label": "17", "text": "Ny 17 §."},
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2018:1381", as_of="2018-08-01")

    assert result["replay_feasible"] is False
    assert result["self_reverse_feasible"] is False
    assert result["recovery_strategy"] == "older_base_required"
    assert result["later_chain_hints"] == []
    assert result["effective_date"] == "2018-08-01"
    assert result["pre_date"] == "2018-07-31"
    assert result["contamination"] == [
        {
            "target_kind": "section",
            "label": "16",
            "issue": "preexisting_renumber_destination",
            "action": "renumber",
            "source_sfs_id": "",
            "origin_hint": "unknown",
            "reverse_patch_candidate": "unknown",
        },
        {
            "target_kind": "section",
            "label": "17",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "",
            "origin_hint": "unknown",
            "reverse_patch_candidate": "unknown",
        },
    ]
    assert result["self_reverse_residual_contamination"] == [
        {
            "target_kind": "section",
            "label": "16",
            "issue": "preexisting_renumber_destination",
            "action": "renumber",
            "source_sfs_id": "",
            "origin_hint": "unknown",
            "reverse_patch_candidate": "unknown",
        },
        {
            "target_kind": "section",
            "label": "17",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "",
            "origin_hint": "unknown",
            "reverse_patch_candidate": "unknown",
        },
    ]


def test_analyze_se_official_replay_feasibility_falls_back_to_base_register_effective_date() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Test.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2018:1381",
                "rubrik": "Ändringsförordning",
                "anteckningar": "ändr. 1 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            }
        ],
    }
    official_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": "Regeringen föreskriver att 1 § förordningen (2015:284) om något ska ha följande lydelse.",
        "effective_clause": "",
        "affected_section_labels": ["1"],
        "provisions": [{"label": "1", "text": "Ny 1 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2018:1381")

    assert result["effective_date"] == "2018-08-01"
    assert result["pre_date"] == "2018-07-31"


def test_analyze_se_official_replay_feasibility_extracts_origin_hints() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "16 § Har betecknats 16 § genom förordning (2018:1381).\n\n"
                "17 § Nytillkommen text. Förordning (2018:1381).\n\n"
                "18 a § Senare text. Förordning (2020:10).\n"
            ),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något dels att nuvarande 17 § ska betecknas 16 §, "
            "dels att den nya 16 § ska ha följande lydelse, dels att det ska införas två nya paragrafer, 17 och 18 a §§, "
            "av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [
            {"label": "16", "text": "Ny 16 §."},
            {"label": "17", "text": "Ny 17 §."},
            {"label": "18a", "text": "Ny 18 a §."},
        ],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2018:1381", as_of="2018-08-01")

    assert result["self_reverse_feasible"] is False
    assert result["recovery_strategy"] == "older_base_required"
    assert result["later_chain_hints"] == [
        {
            "sfs_id": "2020:10",
            "official_act_available": False,
            "pdf_available": False,
            "doc_available": False,
        }
    ]
    assert result["contamination"] == [
        {
            "target_kind": "section",
            "label": "16",
            "issue": "preexisting_renumber_destination",
            "action": "renumber",
            "source_sfs_id": "2018:1381",
            "origin_hint": "renumber_placeholder",
            "reverse_patch_candidate": "yes",
        },
        {
            "target_kind": "section",
            "label": "17",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "2018:1381",
            "origin_hint": "trailing_attribution",
            "reverse_patch_candidate": "yes",
        },
        {
            "target_kind": "section",
            "label": "18a",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "2020:10",
            "origin_hint": "trailing_attribution",
            "reverse_patch_candidate": "no",
        },
    ]
    assert result["self_reverse_residual_contamination"] == [
        {
            "target_kind": "section",
            "label": "17",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "2018:1381",
            "origin_hint": "trailing_attribution",
            "reverse_patch_candidate": "yes",
        },
        {
            "target_kind": "section",
            "label": "18a",
            "issue": "preexisting_insert_target",
            "action": "insert",
            "source_sfs_id": "2020:10",
            "origin_hint": "trailing_attribution",
            "reverse_patch_candidate": "no",
        },
    ]


def test_analyze_se_official_replay_feasibility_detects_available_later_reverse_chain() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "16 § Har betecknats 16 § genom förordning (2020:10).\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2020:10",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "nuvarande 17 a § ska betecknas 16 §; ändr. 16 §",
                "ikraftDateTime": "2020-01-15T00:00:00",
            }
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att nuvarande 17 § ska betecknas 16 §, dels att den nya 16 § ska ha följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [{"label": "16", "text": "Ny 16 §."}],
        "signatories": [],
        "footnotes": [],
    }
    later_act = {
        "sfs_id": "2020:10",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2020-01-15",
        "issued_date": "2020-01-10",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att nuvarande 17 a § ska betecknas 16 §, dels att 16 § ska ha följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17a"],
        "provisions": [{"label": "16", "text": "Senare 16 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2020:10/official.act.json": json.dumps(later_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2018:1381", as_of="2018-08-01")

    assert result["replay_feasible"] is False
    assert result["self_reverse_feasible"] is False
    assert result["later_chain_reverse_feasible"] is True
    assert result["recovery_strategy"] == "older_base_required"
    assert result["later_chain_hints"] == [
        {
            "sfs_id": "2020:10",
            "official_act_available": True,
            "pdf_available": False,
            "doc_available": False,
        }
    ]
    assert result["later_chain_residual_contamination"] == []
    assert result["replay_precondition_ancestry_hints"] == [
        {
            "label": "16",
            "issue": "missing_replace_source",
            "derived_from_label": "17a",
            "via_later_source": "2020:10",
            "direct_later_actions": ["renumber_destination", "replace"],
            "noninvertible_blocker": True,
            "candidate_chain_sfs_ids": [],
        }
    ]


def test_later_chain_reverse_records_skipped_inverse_ops() -> None:
    later_op = LegalOperation(
        op_id="se_test_later_insert_99",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "99"),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label="99",
            children=(IRNode(kind=IRNodeKind.SUBSECTION, text="Later text."),),
        ),
        source=OperationSource(statute_id="se/2020:10", title="Later act"),
    )
    archive = _FakeArchive(
        stored={
            se_official_ops_locator("2020:10"): json.dumps(
                [se_legal_operation_to_dict(later_op)],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )
    statute = IRStatute(
        statute_id="se/2015:284",
        title="Base",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(),
        metadata={},
    )

    patched = _reverse_patch_se_available_later_chain(
        archive,
        statute,
        "2018:1381",
        [{"source_sfs_id": "2020:10"}],
    )

    assert patched.metadata["later_chain_reverse_applied"] is True
    adjudications = patched.metadata["later_chain_reverse_adjudications"]
    assert len(adjudications) == 1
    assert adjudications[0]["kind"] == "se_replay_target_not_found"
    assert adjudications[0]["source_statute"] == "se/2020:10"
    assert adjudications[0]["op_id"] == "se_reverse_insert_2020:10_99_1"
    assert adjudications[0]["detail"]["rule_id"] == "se_replay_target_not_found"
    assert adjudications[0]["detail"]["phase"] == "replay"
    assert adjudications[0]["detail"]["family"] == "unsupported_or_unresolved_action"
    assert adjudications[0]["detail"]["blocking"] is True
    assert adjudications[0]["detail"]["strict_disposition"] == "block"
    assert adjudications[0]["detail"]["quirks_disposition"] == "record"
    assert adjudications[0]["detail"]["reverse_source_sfs_id"] == "2020:10"


def test_analyze_se_official_replay_feasibility_classifies_noninvertible_placeholder_blocker() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "16 § Senare 16 §. Förordning (2026:63).\n\n17 a § Har betecknats 16 § genom förordning (2026:63).\n"
            ),
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    target_act = {
        "sfs_id": "2021:201",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2021-04-20",
        "issued_date": "2021-04-15",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "att det ska införas en ny paragraf, 17 a §, av följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": [],
        "provisions": [{"label": "17a", "text": "Ny 17 a §."}],
        "signatories": [],
        "footnotes": [],
    }
    later_act = {
        "sfs_id": "2026:63",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2026-02-10",
        "issued_date": "2026-02-05",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att 16 § ska upphöra att gälla, dels att nuvarande 17 a § ska betecknas 16 §."
        ),
        "effective_clause": "",
        "affected_section_labels": ["16"],
        "provisions": [],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2021:201/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:63/official.act.json": json.dumps(later_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:63/official.ops.json": json.dumps(
                [
                    {
                        "op_id": "repeal_16",
                        "sequence": 1,
                        "action": "repeal",
                        "target": {"path": [["section", "16"]], "special": None},
                        "targets": [{"path": [["section", "16"]], "special": None}],
                        "payload": None,
                        "anchor": None,
                        "destination": None,
                        "source": None,
                        "applicability": [],
                        "provenance_tags": [],
                        "text_match": None,
                        "text_replacement": None,
                        "text_occurrence": 0,
                        "group_id": None,
                    },
                    {
                        "op_id": "renumber_17a_to_16",
                        "sequence": 2,
                        "action": "renumber",
                        "target": {"path": [["section", "17a"]], "special": None},
                        "targets": [{"path": [["section", "17a"]], "special": None}],
                        "payload": None,
                        "anchor": None,
                        "destination": {"path": [["section", "16"]], "special": None},
                        "source": None,
                        "applicability": [],
                        "provenance_tags": [],
                        "text_match": None,
                        "text_replacement": None,
                        "text_occurrence": 0,
                        "group_id": None,
                    },
                ],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    result = analyze_se_official_replay_feasibility(archive, "2021:201", as_of="2021-05-01")

    assert result["later_chain_reverse_feasible"] is False
    assert result["recovery_strategy"] == "older_base_required"


def test_plan_se_older_base_rebuild_reports_chain_statuses() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "16 § Test.\n17 § Test.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2016:13",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ändr. 4 §",
                "ikraftDateTime": "2016-03-01T00:00:00",
            },
            {
                "beteckning": "2018:11",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ny 7 a §",
                "ikraftDateTime": "2018-03-01T00:00:00",
            },
            {
                "beteckning": "2018:1381",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ny 17 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            },
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att nuvarande 17 § ska betecknas 16 §, dels att den nya 16 § ska ha följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [{"label": "16", "text": "Ny 16 §."}],
        "signatories": [],
        "footnotes": [],
    }
    prior_compiled_act = {
        "sfs_id": "2016:13",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2016-02-20",
        "issued_date": "2016-02-18",
        "enacting_clause": "Regeringen föreskriver att 4 § förordningen (2015:284) om något ska ha följande lydelse.",
        "effective_clause": "",
        "affected_section_labels": ["4"],
        "provisions": [{"label": "4", "text": "Ny 4 §."}],
        "signatories": [],
        "footnotes": [],
    }
    base_official = {
        "sfs_id": "2015:284",
        "title": "Förordning (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "",
        "is_amending_act": False,
        "published_date": "2015-05-21",
        "issued_date": "2015-05-21",
        "enacting_clause": "",
        "effective_clause": "",
        "affected_section_labels": [],
        "provisions": [{"label": "16", "text": "Bas 16 §."}, {"label": "17", "text": "Bas 17 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2015:284/official.act.json": json.dumps(base_official, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2016:13/official.act.json": json.dumps(prior_compiled_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = plan_se_older_base_rebuild(archive, "2018:1381")

    assert result["effective_date"] == "2018-08-01"
    assert result["pre_date"] == "2018-07-31"
    assert result["base_seed"] == {
        "sfs_id": "2015:284",
        "official_act_available": True,
        "official_base_ir_available": False,
        "pdf_available": False,
        "doc_available": False,
    }
    assert result["prior_amendment_count"] == 2
    assert result["compiled_count"] == 1
    assert result["missing_official_count"] == 1
    assert result["unsupported_count"] == 0
    assert result["chain_diagnostics"] == (
        {
            "rule_id": "se_official_rebuild_chain_missing_official_act",
            "phase": "acquisition",
            "family": "source_pathology",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "sfs_id": "2018:11",
            "effective_date": "2018-03-01",
            "scope_text": "ny 7 a §",
            "ops_status": "missing_official_act",
            "error": "",
            "reason": "prior Sweden amendment official act is unavailable",
        },
    )
    assert result["official_chain_ready"] is False
    assert result["seed_ready"] is False
    assert result["rebuild_ready"] is False
    assert result["chain"] == [
        {
            "sfs_id": "2016:13",
            "effective_date": "2016-03-01",
            "title": "Förordning om ändring i förordningen (2015:284) om något",
            "scope_text": "ändr. 4 §",
            "official_act_available": True,
            "pdf_available": False,
            "doc_available": False,
            "ops_status": "compiled",
            "op_count": 1,
            "error": "",
        },
        {
            "sfs_id": "2018:11",
            "effective_date": "2018-03-01",
            "title": "Förordning om ändring i förordningen (2015:284) om något",
            "scope_text": "ny 7 a §",
            "official_act_available": False,
            "pdf_available": False,
            "doc_available": False,
            "ops_status": "missing_official_act",
            "op_count": 0,
            "error": "",
        },
    ]


def test_plan_se_older_base_rebuild_attaches_public_source_probe(monkeypatch) -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "16 § Test.\n17 § Test.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2018:11",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ny 7 a §",
                "ikraftDateTime": "2018-03-01T00:00:00",
            },
            {
                "beteckning": "2018:1381",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ny 17 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            },
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "dels att nuvarande 17 § ska betecknas 16 §, dels att den nya 16 § ska ha följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [{"label": "16", "text": "Ny 16 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
        }
    )
    monkeypatch.setattr(
        "lawvm.sweden.fetch.probe_se_public_source_status",
        lambda sfs_id: {
            "doc_status": "cloudflare_blocked",
            "pdf_status": "not_found",
            "resolved_pdf_url": "",
            "public_source_viable": False,
        },
    )

    result = plan_se_older_base_rebuild(archive, "2018:1381", probe_sources=True)

    assert result["base_seed"]["public_source_probe"] == {
        "doc_status": "cloudflare_blocked",
        "pdf_status": "not_found",
        "resolved_pdf_url": "",
        "public_source_viable": False,
    }
    assert result["chain"][0]["public_source_probe"] == {
        "doc_status": "cloudflare_blocked",
        "pdf_status": "not_found",
        "resolved_pdf_url": "",
        "public_source_viable": False,
    }


def test_plan_se_older_base_rebuild_surfaces_fetch_missing_failure_as_typed_diagnostic(monkeypatch) -> None:
    # Guard-liveness (§2.9): ``fetch_missing=True`` is a best-effort acquisition
    # lane, but a fetch failure MUST NOT vanish silently — §1.10 forbids the
    # ``except Exception: return`` shape that would let replay proceed against
    # an empty base_seed with ``official_act_available=False`` and no diagnostic,
    # disguising an acquisition fault as "no archived act." Drive a known-
    # raising ``fetch_se_official_artifacts`` through the production path and
    # assert the failure surfaces as a named diagnostic on ``base_seed``.
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "16 § Test.\n17 § Test.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2018:1381",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ny 17 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            },
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": (
            "Regeringen föreskriver i fråga om förordningen (2015:284) om något "
            "att den nya 17 § ska ha följande lydelse."
        ),
        "effective_clause": "",
        "affected_section_labels": ["17"],
        "provisions": [{"label": "17", "text": "Ny 17 §."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    def _raise_fetch_failure(sfs_id, archive_obj, force_reextract=False):
        raise ConnectionError(f"simulated cloudflare block for sfs://{sfs_id}")

    monkeypatch.setattr("lawvm.sweden.fetch.fetch_se_official_artifacts", _raise_fetch_failure)

    result = plan_se_older_base_rebuild(archive, "2018:1381", fetch_missing=True)

    # §1.10 named diagnostic: an identifiable failure record (not a generic
    # "missing act" 404), carrying the sfs_id + exception type + message so
    # the acquisition fault is observable downstream instead of disguised
    # as "no archived act."
    failures = result["base_seed"].get("official_act_acquisition_failures")
    assert failures, "fetch_missing acquisition failure did not surface on base_seed (§1.10 silent swallow regression)"
    assert any(
        failure.get("rule_id") == "se_official_artifacts_fetch_failed"
        and failure.get("sfs_id") == "2015:284"
        and failure.get("error_type") == "ConnectionError"
        and "simulated cloudflare block" in failure.get("error_message", "")
        # §1.10 honesty: this is a fetch-time IO/utility boundary (network
        # acquisition of an archived SFS act), so there is no source clause
        # text in scope to embed — ``clause_text`` is the empty string rather
        # than a fabricated source-text witness. The residual is named/
        # witnessed by ``rule_id`` + ``sfs_id`` + ``error_type`` +
        # ``error_message``; the explicit empty ``clause_text`` documents the
        # §1.10 fact rather than faking content (mirrors ``named_swallow``
        # module docstring's IO/utility-boundary exception).
        and failure.get("clause_text", "MISSING") == ""
        for failure in failures
    ), f"acquisition failure diagnostic missing required fields (incl. clause_text): {failures}"
    # And the surface lane still records the unavailability honestly.
    assert result["base_seed"]["official_act_available"] is False


def test_plan_se_older_base_rebuild_reports_base_seed_when_available() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Test.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2018:1381",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ändr. 1 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            }
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": "Regeringen föreskriver att 1 § förordningen (2015:284) om något ska ha följande lydelse.",
        "effective_clause": "",
        "affected_section_labels": ["1"],
        "provisions": [{"label": "1", "text": "Ny 1 §."}],
        "signatories": [],
        "footnotes": [],
    }
    base_ir = build_se_official_base_statute(
        {
            "sfs_id": "2015:284",
            "title": "Förordning (2015:284) om något",
            "act_type": "förordning",
            "is_amending_act": False,
            "published_date": "2015-01-01",
            "issued_date": "2015-01-01",
            "provisions": [{"label": "1", "text": "Bas 1 §."}],
            "inserted_headings": [],
            "appendices": [],
        }
    )
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2015:284/official.act.json": json.dumps(
                {
                    "sfs_id": "2015:284",
                    "title": "Förordning (2015:284) om något",
                    "act_type": "förordning",
                    "is_amending_act": False,
                    "provisions": [{"label": "1", "text": "Bas 1 §."}],
                    "inserted_headings": [],
                    "appendices": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            "se://sfs/2015:284/official.base.ir.json": json.dumps(base_ir.to_jsonable_dict(), ensure_ascii=False).encode(
                "utf-8"
            ),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = plan_se_older_base_rebuild(archive, "2018:1381")

    assert result["base_seed"] == {
        "sfs_id": "2015:284",
        "official_act_available": True,
        "official_base_ir_available": True,
        "pdf_available": False,
        "doc_available": False,
    }
    assert result["official_chain_ready"] is True
    assert result["seed_ready"] is True
    assert result["rebuild_ready"] is True


def test_rebuild_se_older_base_from_official_chain_applies_compiled_prior_ops() -> None:
    base_payload = {
        "beteckning": "2015:284",
        "rubrik": "Förordning (2015:284) om något",
        "ikraftDateTime": "2015-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2015-01-01T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Nuvarande text.\n",
        },
        "publiceradDateTime": "2015-01-01T00:00:00",
        "andringsforfattningar": [
            {
                "beteckning": "2016:13",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ändr. 1 §",
                "ikraftDateTime": "2016-03-01T00:00:00",
            },
            {
                "beteckning": "2018:1381",
                "rubrik": "Förordning om ändring i förordningen (2015:284) om något",
                "anteckningar": "ändr. 1 §",
                "ikraftDateTime": "2018-08-01T00:00:00",
            },
        ],
    }
    target_act = {
        "sfs_id": "2018:1381",
        "title": "Förordning om ändring i förordningen (2015:284) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2015:284",
        "is_amending_act": True,
        "published_date": "2018-07-31",
        "issued_date": "2018-07-26",
        "enacting_clause": "Regeringen föreskriver att 1 § förordningen (2015:284) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 1 augusti 2018.",
        "affected_section_labels": ["1"],
        "provisions": [{"label": "1", "text": "Måltext."}],
        "signatories": [],
        "footnotes": [],
    }
    base_ir = build_se_official_base_statute(
        {
            "sfs_id": "2015:284",
            "title": "Förordning (2015:284) om något",
            "act_type": "förordning",
            "is_amending_act": False,
            "published_date": "2015-01-01",
            "issued_date": "2015-01-01",
            "provisions": [{"label": "1", "text": "Bas 1 §."}],
            "inserted_headings": [],
            "appendices": [],
        }
    )
    prior_ops = compile_se_official_act_ops(
        {
            "sfs_id": "2016:13",
            "title": "Förordning om ändring i förordningen (2015:284) om något",
            "act_type": "förordning",
            "amended_act_sfs_id": "2015:284",
            "is_amending_act": True,
            "published_date": "2016-02-10",
            "issued_date": "2016-02-05",
            "enacting_clause": "Regeringen föreskriver att 1 § förordningen (2015:284) om något ska ha följande lydelse.",
            "effective_clause": "Denna förordning träder i kraft den 1 mars 2016.",
            "affected_section_labels": ["1"],
            "provisions": [{"label": "1", "text": "Kedjetext."}],
            "inserted_headings": [],
            "appendices": [],
            "signatories": [],
            "footnotes": [],
        },
        source_id="2016:13",
    )
    archive = _FakeArchive(
        stored={
            "se://sfs/2015:284/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2015:284/official.act.json": json.dumps(
                {
                    "sfs_id": "2015:284",
                    "title": "Förordning (2015:284) om något",
                    "act_type": "förordning",
                    "is_amending_act": False,
                    "provisions": [{"label": "1", "text": "Bas 1 §."}],
                    "inserted_headings": [],
                    "appendices": [],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            "se://sfs/2015:284/official.base.ir.json": json.dumps(base_ir.to_jsonable_dict(), ensure_ascii=False).encode(
                "utf-8"
            ),
            "se://sfs/2018:1381/official.act.json": json.dumps(target_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2016:13/official.act.json": json.dumps(
                {
                    "sfs_id": "2016:13",
                    "title": "Förordning om ändring i förordningen (2015:284) om något",
                    "act_type": "förordning",
                    "amended_act_sfs_id": "2015:284",
                    "is_amending_act": True,
                    "effective_clause": "Denna förordning träder i kraft den 1 mars 2016.",
                    "provisions": [{"label": "1", "text": "Kedjetext."}],
                    "affected_section_labels": ["1"],
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            "se://sfs/2016:13/official.ops.json": json.dumps(
                [se_legal_operation_to_dict(op) for op in prior_ops],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    rebuilt = rebuild_se_older_base_from_official_chain(archive, "2018:1381")

    assert se_section_text_map(rebuilt)["1"] == "Kedjetext."
    assert rebuilt.metadata["historical_rebuild_for"] == "2018:1381"


def test_check_se_official_replay_filters_preexisting_base_invariant_noise() -> None:
    base_payload = {
        "beteckning": "2026:106",
        "rubrik": "Förordning (2026:106) om något",
        "ikraftDateTime": "2026-04-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Justitiedepartementet", "namnOchEnhet": "Justitiedepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2026-02-26T00:00:00",
            "andringInford": None,
            "forfattningstext": (
                "2 § /Upphör att gälla U:2026-04-15/\nGamla lydelsen.\n\n"
                "2 § /Träder i kraft I:2026-04-15/\nNya lydelsen. Förordning (2026:286).\n\n"
                "Bilaga 1\n1. Alpha\n\na. Dublett\n\na. Dublett två\n"
            ),
        },
        "publiceradDateTime": "2026-03-23T12:17:32",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:106) om något",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:106",
        "is_amending_act": True,
        "published_date": "2026-03-24",
        "issued_date": "2026-03-19",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:106) om något ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "2", "text": "Nya lydelsen."}],
        "signatories": [],
        "footnotes": [],
    }
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:106/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["invariant_violations"] == []


def test_build_se_source_bundle_combines_source_record_and_current_statute() -> None:
    doc = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    html = '<a href="/sites/default/files/sfs/2025-05/SFS2025-399.pdf">PDF</a>'

    bundle = build_se_source_bundle(json.dumps(doc).encode("utf-8"), doc_html=html)

    assert bundle.source_record.sfs_id == "2025:399"
    assert bundle.source_record.source_urls.official_sfs_pdf_url == (
        "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf"
    )
    assert bundle.current_statute.statute_id == "2025:399"


def test_archive_se_source_bundle_stores_json_source_record_and_ir() -> None:
    doc = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    archive = _FakeArchive()
    doc_html = b'<a href="/sites/default/files/sfs/2025-05/SFS2025-399.pdf">PDF</a>'

    bundle = archive_se_source_bundle(json.dumps(doc).encode("utf-8"), archive, doc_html=doc_html)

    assert bundle.source_record.sfs_id == "2025:399"
    assert se_rk_current_json_locator("2025:399") in archive.stored
    assert se_source_record_locator("2025:399") in archive.stored
    assert se_current_ir_locator("2025:399") in archive.stored
    assert se_bundle_manifest_locator("2025:399") in archive.stored
    assert se_official_doc_locator("2025:399") in archive.stored


def test_archive_loaders_round_trip_bundle_record_and_ir() -> None:
    doc = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    archive = _FakeArchive()
    archive_se_source_bundle(json.dumps(doc).encode("utf-8"), archive)

    record = load_se_source_record_from_archive(archive, "2025:399")
    ir = load_se_current_ir_from_archive(archive, "2025:399")
    bundle = load_se_bundle_from_archive(archive, "2025:399")

    assert record is not None
    assert record["sfs_id"] == "2025:399"
    assert ir is not None
    assert ir["statute_id"] == "2025:399"
    assert bundle is not None
    assert bundle["source_record"]["sfs_id"] == "2025:399"


def test_attach_official_artifacts_to_bundle_updates_urls() -> None:
    doc = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    bundle = build_se_source_bundle(json.dumps(doc).encode("utf-8"))
    official = fetch_se_official_artifacts(
        "2025:399",
        _FakeArchive(
            fetched={
                "https://svenskforfattningssamling.se/doc/2025399.html": b'<a href="/sites/default/files/sfs/2025-05/SFS2025-399.pdf">PDF</a>',
                "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf": b"%PDF-1.7",
            },
        ),
        force_reextract=True,
    )
    assert official is not None

    merged = attach_official_artifacts_to_bundle(bundle, official)
    assert merged.official_artifacts is not None
    assert merged.source_record.source_urls.official_sfs_pdf_url == official.pdf_url


def test_fetch_official_updates_existing_bundle_manifest() -> None:
    doc = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    archive = _FakeArchive(
        fetched={
            "https://svenskforfattningssamling.se/doc/2025399.html": b'<a href="/sites/default/files/sfs/2025-05/SFS2025-399.pdf">PDF</a>',
            "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf": b"%PDF-1.7",
        },
    )
    archive_se_source_bundle(json.dumps(doc).encode("utf-8"), archive)

    official = fetch_se_official_artifacts("2025:399", archive, force_reextract=True)

    assert official is not None
    bundle = load_se_bundle_from_archive(archive, "2025:399")
    assert bundle is not None
    assert bundle["official_artifacts"]["pdf_url"] == official.pdf_url
    assert bundle["source_record"]["source_urls"]["official_sfs_pdf_url"] == official.pdf_url


def test_fetch_official_falls_back_to_rk_issue_date_guess(monkeypatch) -> None:
    archive = _FakeArchive(
        fetched={
            se_rk_current_url("2025:399"): '<span class="bold">Utfärdad:</span> 2025-05-22'.encode("utf-8"),
            "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf": b"%PDF-1.7",
        },
    )
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")

    bundle = fetch_se_official_artifacts("2025:399", archive)

    assert bundle is not None
    assert bundle.pdf_url == "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf"


def test_fetch_se_rk_current_json_stores_source_document(monkeypatch) -> None:
    archive = _FakeArchive()
    source = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    response = {"hits": {"hits": [{"_source": source}]}}
    monkeypatch.setattr(
        "lawvm.sweden.fetch._curl_json_post",
        lambda url, headers, payload: json.dumps(response).encode("utf-8"),
    )

    current_json = fetch_se_rk_current_json("2025:399", archive)

    assert current_json is not None
    assert json.loads(current_json.decode("utf-8"))["beteckning"] == "2025:399"
    assert se_rk_current_json_locator("2025:399") in archive.stored


def test_fetch_se_rk_current_json_records_fetch_failure(monkeypatch) -> None:
    archive = _FakeArchive()
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        "lawvm.sweden.fetch._curl_json_post",
        lambda url, headers, payload: None,
    )

    current_json = fetch_se_rk_current_json("2025:399", archive, diagnostics_out=diagnostics)

    assert current_json is None
    assert diagnostics == [
        {
            "rule_id": "se_rk_current_fetch_failed",
            "family": "source_pathology",
            "phase": "acquisition",
            "reason": "Sweden RK current JSON request returned no payload",
            "sfs_id": "2025:399",
            "locator": se_rk_current_json_locator("2025:399"),
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_fetch_se_rk_current_json_records_empty_hits(monkeypatch) -> None:
    archive = _FakeArchive()
    diagnostics: list[dict[str, object]] = []
    monkeypatch.setattr(
        "lawvm.sweden.fetch._curl_json_post",
        lambda url, headers, payload: b'{"hits": {"hits": []}}',
    )

    current_json = fetch_se_rk_current_json("2025:399", archive, diagnostics_out=diagnostics)

    assert current_json is None
    assert diagnostics == [
        {
            "rule_id": "se_rk_current_no_hits",
            "family": "source_pathology",
            "phase": "acquisition",
            "reason": "Sweden RK current JSON response contained no published SFS hit",
            "sfs_id": "2025:399",
            "locator": se_rk_current_json_locator("2025:399"),
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        }
    ]


def test_hydrate_se_bundle_live_archives_bundle_and_official_artifacts(monkeypatch) -> None:
    archive = _FakeArchive(
        fetched={
            se_rk_current_url("2025:399"): b'<span class="bold">Utf\xc3\xa4rdad:</span> 2025-05-22',
            "https://svenskforfattningssamling.se/sites/default/files/sfs/2025-05/SFS2025-399.pdf": b"%PDF-1.7",
        },
    )
    source = {
        "beteckning": "2025:399",
        "rubrik": "Förordning (2025:399) om riktålder för pension för år 2031",
        "ikraftDateTime": "2025-07-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": {
            "utfardadDateTime": "2025-05-22T00:00:00",
            "andringInford": None,
            "forfattningstext": "1 § Testbestämmelse.",
        },
        "publiceradDateTime": "2025-05-26T08:54:29.3888676",
        "andringsforfattningar": [],
    }
    response = {"hits": {"hits": [{"_source": source}]}}
    monkeypatch.setattr(
        "lawvm.sweden.fetch._curl_json_post",
        lambda url, headers, payload: json.dumps(response).encode("utf-8"),
    )
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "Recovered PDF text")

    bundle = hydrate_se_bundle_live("2025:399", archive)

    assert bundle is not None
    assert bundle.source_record.sfs_id == "2025:399"
    assert bundle.official_artifacts is not None
    assert se_rk_current_json_locator("2025:399") in archive.stored
    assert se_source_record_locator("2025:399") in archive.stored
    assert se_current_ir_locator("2025:399") in archive.stored
    assert se_bundle_manifest_locator("2025:399") in archive.stored
    archived_bundle = load_se_bundle_from_archive(archive, "2025:399")
    assert archived_bundle is not None
    assert archived_bundle["official_artifacts"]["pdf_url"] == bundle.official_artifacts.pdf_url


# --------------------------------------------------------------------------- #
# §2.9 production-path fire-drills for the KNOW-01 overwrite_event_ledger.     #
# --------------------------------------------------------------------------- #


def test_fetch_se_official_artifacts_force_reextract_fires_overwrite_events(monkeypatch) -> None:
    """§2.9 guard-liveness production-path fire-drill for the KNOW-01 overwrite
    ledger wired at fetch_se_official_artifacts.

    The worst failure class: a check that exists and is registered but
    unreachable from the production lane. The wrapper at
    ``se_store_with_overwrite_event`` is wired at the pdf_text +
    cleaned_text + act_json + base_ir overwrite sites INSIDE
    fetch_se_official_artifacts — but a unit test of the wrapper alone
    cannot prove that. This test drives a real overwrite through the
    production lane:

    * seeds prior bytes at the pdf.text / cleaned.txt / official.act.json
      locators so re-extraction overlays existing manifestations;
    * monkeypatches the lower-level fetch + parse helpers (no network);
    * force_reextract=True through the production function;
    * asserts the accumulator receives events at the text + cleaned + act_json
      locators with the seeded prior content's hash as prior_bytes_sha256
      (NOT blank — the prior manifestation was overwritten; KNOW-01 demands
      its hash be recorded or the mutation is silent).
    """
    import hashlib

    from lawvm.sweden.se_overwrite_event_ledger import SEOverwriteEvent

    sfs_id = "2026:286"
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    prior_text = b"prior body text"
    prior_cleaned = b"prior cleaned text"
    prior_act_json = b'{"prior_act": true}'

    text_locator = "se://sfs/2026:286/official.pdf.txt"
    cleaned_locator = "se://sfs/2026:286/official.cleaned.txt"
    act_json_locator = "se://sfs/2026:286/official.act.json"

    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        },
        stored={
            text_locator: prior_text,
            cleaned_locator: prior_cleaned,
            act_json_locator: prior_act_json,
        },
    )
    monkeypatch.setattr(
        "lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "New extracted PDF text"
    )

    overwrite_events: list[SEOverwriteEvent] = []
    bundle = fetch_se_official_artifacts(
        "2026:286",
        archive,
        force_reextract=True,
        overwrite_events_out=overwrite_events,
    )

    assert bundle is not None
    # §2.9: the wrapper actually FIRED (parameter not dead).
    assert overwrite_events, (
        "force_reextract=True drive through fetch_se_official_artifacts emitted "
        "NO overwrite events — overwrite_events_out is unreachable from the "
        "production lane (§2.9 guard-liveness failure)."
    )
    events_by_locator = {event.locator: event for event in overwrite_events}

    # KNOW-01 load-bearing: prior manifestation hash recorded for the text locator.
    assert text_locator in events_by_locator
    text_event = events_by_locator[text_locator]
    assert text_event.prior_bytes_sha256 == "sha256:" + hashlib.sha256(prior_text).hexdigest()
    assert archive.get(text_locator) == b"New extracted PDF text"
    assert text_event.source_trigger == "force_reextract"
    assert text_event.rule_id == "se_official_artifacts_force_reextract_overwrite"

    # Downstream-derived site: act_json must carry the prior act_json's hash
    # (KNOW-01 for the parsed-act-text manifestation).
    assert act_json_locator in events_by_locator
    act_json_event = events_by_locator[act_json_locator]
    assert (
        act_json_event.prior_bytes_sha256
        == "sha256:" + hashlib.sha256(prior_act_json).hexdigest()
    )


def test_fetch_se_official_artifacts_force_reextract_blank_prior_on_first_write(monkeypatch) -> None:
    """Negative discipline: when force_reextract re-extracts text at a locator
    with NO prior bytes (fresh first-write inside the re-extract branch), the
    event's prior_bytes_sha256 is blank — the event records "new manifestation
    created" rather than "matter mutated". Proves the wrapper's read-before-
    write discipline (it doesn't silently emit a stale hash on a fresh locator)."""
    from lawvm.sweden.se_overwrite_event_ledger import SEOverwriteEvent

    sfs_id = "2026:999"
    doc_url = "https://svenskforfattningssamling.se/doc/2026999.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-12/SFS2026-999.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-12/SFS2026-999.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    monkeypatch.setattr(
        "lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes, findings_out=None: "First extracted text"
    )

    overwrite_events: list[SEOverwriteEvent] = []
    bundle = fetch_se_official_artifacts(
        "2026:999",
        archive,
        force_reextract=True,
        overwrite_events_out=overwrite_events,
    )
    assert bundle is not None

    text_locator = "se://sfs/2026:999/official.pdf.txt"
    events_by_locator = {event.locator: event for event in overwrite_events}
    assert events_by_locator[text_locator].prior_bytes_sha256 == "", (
        "force_reextract on a fresh locator emitted a non-empty prior_bytes_sha256 — "
        "the wrapper's read-before-write discipline is broken."
    )


def test_check_se_official_replay_propagates_partial_adjudications_on_apply_raise(
    monkeypatch,
) -> None:
    """§2.9 + §1.0/§1.8/§1.10 fire-drill (silent-failure review HIGH #3):

    When ``apply_se_ops_conserved`` raises mid-apply, the production lane
    ``check_se_official_replay`` MUST:

    * preserve the partial adjudication witnesses emitted BEFORE the raise on
      the returned ``adjudications`` list (the §1.0 "evidence is not silently
      destroyed" + §1.8 "no unsupported lane disappears" contracts). Pre-fix
      state: the SE production caller had NO try/except at the apply call site
      — bare-apply raised raw, the local ``replay_adjudications`` list was
      discarded entirely by the propagating exception.
    * append a typed ``se_replay_apply_raise`` orchestration adjudication per
      §1.10 embed-exception-as-clause-text rule (so a downstream consumer can
      diagnose the apply raise without re-running extraction);
    * return a structured ``outcome='apply_raise'`` /
      ``reason_code='se_replay_apply_raise'`` dict mirroring the existing
      ``_se_replay_unresolved_outcome`` shape so the scan-lane
      ``typed_outcome != SE_REPLAY_OUTCOME_REPLAY_FEASIBLE`` dispatcher
      (line ~3880) buckets it correctly downstream.

    Mirrors ``test_replay_ee_to_pit_propagates_partial_adjudications_on_apply_raise``
    (the EE production-caller fire-drill), the NO precedent
    ``test_replay_no_to_pit_strict_action_family_rejects_recovery`` (end-to-end
    assertion shape), and the upstream-phase fixture pattern of
    ``test_check_se_official_replay_collects_skipped_replay_ops_as_adjudications``
    (the SE production-routing happy-path test). Iter2 W2 closed the
    conserved-wrapper propagate-on-raise contract; iter3 W3 (this test) pins
    the production caller's half of the contract — the wrapper's propagation
    is unreachable from production unless the caller's on-raise wrap surfaces
    the partial list on the returned dict.
    """
    base_payload = {
        "beteckning": "2026:777",
        "rubrik": "Förordning (2026:777) om test",
        "ikraftDateTime": "2026-01-01T00:00:00",
        "ikraftOvergangsbestammelse": False,
        "organisation": {"namn": "Socialdepartementet", "namnOchEnhet": "Socialdepartementet"},
        "forfattningstypNamn": "Förordning",
        "register": {"forarbeten": None},
        "fulltext": (
            "2 § /Upphör att gälla U:2026-04-15/\n"
            "Gammal lydelse.\n\n"
            "2 § /Träder i kraft I:2026-04-15/\n"
            "Ny lydelse. Förordning (2026:286).\n"
        ),
        "publiceradDateTime": "2026-01-01T00:00:00",
        "andringsforfattningar": [],
    }
    official_act = {
        "sfs_id": "2026:286",
        "title": "Förordning om ändring i förordningen (2026:777) om test",
        "act_type": "förordning",
        "amended_act_sfs_id": "2026:777",
        "is_amending_act": True,
        "published_date": "2026-04-20",
        "issued_date": "2026-04-18",
        "enacting_clause": "Regeringen föreskriver att 2 § förordningen (2026:777) om test ska ha följande lydelse.",
        "effective_clause": "Denna förordning träder i kraft den 15 april 2026.",
        "affected_section_labels": ["2"],
        "provisions": [{"label": "2", "text": "Ny lydelse."}],
        "signatories": [],
        "footnotes": [],
    }
    valid_op = LegalOperation(
        op_id="se_official_replace_2",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny lydelse."),
        source=OperationSource(statute_id="2026:286", effective="2026-04-15"),
    )
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:777/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.ops.json": json.dumps(
                [se_legal_operation_to_dict(op) for op in [valid_op]],
                ensure_ascii=False,
            ).encode("utf-8"),
        }
    )

    raise_message = "synthesized mid-apply raise (e.g. se strict_action_family=True)"

    # Spy: replace ``apply_se_ops_conserved`` in the fetch module with a
    # wrapper that (a) appends a known pre-raise adjudication to
    # ``adjudications_out`` (mirroring what bare apply does when it processes
    # the synthesized skip op BEFORE the §1.10 fail-loud raise), then (b)
    # raises ValueError. Mirrors the NO precedent at
    # ``test_apply_no_ops_conserved_propagates_recovery_adjudication_on_raise``
    # and the EE/EU fire-drill tests — bare apply first emits the skip then
    # raises; here the skip + raise is wired through the spy so the SE
    # production caller's on-raise handling is reached through the FULL
    # ``check_se_official_replay`` path (the §2.9 guard-liveness discipline).
    def spy_apply_se_ops_conserved(statute, ops, **kwargs):
        adjudications_out = kwargs.get("adjudications_out")
        if adjudications_out is not None:
            adjudications_out.append(
                CompileAdjudication(
                    kind="se_replay_target_not_found",
                    message=(
                        "Synthesized pre-raise skip adjudication — op target "
                        "not in the baseline body (mirrors bare-apply's per-op "
                        "skip emission BEFORE the §1.10 fail-loud raise)."
                    ),
                    source_statute="2026:286",
                    blocking=False,
                    phase="replay",
                    op_id="se_official_replace_2",
                    detail={
                        "rule_id": "se_replay_target_not_found",
                        "phase": "replay",
                        "blocking": False,
                    },
                )
            )
        raise ValueError(raise_message)

    monkeypatch.setattr(
        "lawvm.sweden.fetch.apply_se_ops_conserved",
        spy_apply_se_ops_conserved,
    )

    result = check_se_official_replay(archive, "2026:286")

    # The apply raise is surfaced as a structured ``outcome='apply_raise'`` /
    # ``reason_code='se_replay_apply_raise'`` dict (mirrors the existing
    # ``_se_replay_unresolved_outcome`` shape from the precondition-issues /
    # older-base-required branches). Pre-fix the raw exception propagated to
    # the caller and there was no structured return at all.
    assert result["outcome"] == "apply_raise", (
        f"result['outcome'] is {result['outcome']!r}, expected 'apply_raise' — "
        "the production caller's on-raise handling regressed (§2.9 worst-class "
        "silent failure: a guard that exists but cannot fire)."
    )
    assert result["reason_code"] == "se_replay_apply_raise"
    assert raise_message in result["message"]
    assert result["target_count"] == 0  # apply lane did not run
    assert result["match_count"] == 0
    assert result["rows"] == []

    # §1.0 / §1.8 partial-witness preservation: the pre-raise skip adjudication
    # emitted by the spy IS on ``result['adjudications']``. Pre-fix the local
    # list was discarded by the propagating exception (silent-failure review
    # HIGH #3).
    pre_raise = [
        a for a in result["adjudications"] if a.get("kind") == "se_replay_target_not_found"
    ]
    assert pre_raise, (
        "result['adjudications'] does not carry the pre-raise "
        "se_replay_target_not_found witness — the §1.0/§1.8 partial-loss "
        "failure (silent-failure review HIGH #3: pre-fix the raw exception "
        "discarded replay_adjudications before the success-path dict "
        "construction projected it onto 'adjudications')."
    )
    assert pre_raise[0]["op_id"] == "se_official_replace_2"

    # §1.10 typed orchestration adjudication: ``se_replay_apply_raise`` IS on
    # the returned dict's ``adjudications`` list with ``exception_type`` /
    # ``exception`` / ``clause_text`` embedded in its ``detail`` dict.
    orchestration = next(
        (a for a in result["adjudications"] if a.get("kind") == "se_replay_apply_raise"),
        None,
    )
    assert orchestration is not None, (
        "result['adjudications'] does not carry the typed "
        "se_replay_apply_raise orchestration adjudication — the §1.10 "
        "embed-snippet contract is unmet (silent-failure review HIGH #3)."
    )
    assert orchestration["detail"]["exception_type"] == "ValueError"
    assert orchestration["detail"]["exception"] == raise_message
    assert orchestration["detail"]["clause_text"] == raise_message  # ≤400 chars
    # The orchestration adjudication is non-blocking — it is a WITNESS, not
    # the gate (mirrors the EE conserved-wrapper's
    # ``RejectedItem.blocking=False`` pattern). The blocking gate lives on
    # the structured ``outcome='apply_raise'`` signal — the SE convention for
    # apply-fold failure that the scan-lane typed-outcome dispatcher keys on.
    assert orchestration["blocking"] is False
    assert orchestration["phase"] == "replay"
    assert orchestration["source_statute"] == "2026:777"  # resolved base sfs
    assert orchestration["detail"]["rule_id"] == "se_replay_apply_raise"
    assert orchestration["detail"]["family"] == "orchestration_failure"

    # The structured exception fields are also projected onto
    # ``outcome_detail`` so downstream dispatch (e.g. scan_se_official_replay_act)
    # can route on them without re-parsing the typed adjudication ledger.
    assert result["outcome_detail"]["exception_type"] == "ValueError"
    assert result["outcome_detail"]["exception"] == raise_message
    assert result["outcome_detail"]["clause_text"] == raise_message
