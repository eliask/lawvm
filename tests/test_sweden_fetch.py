from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import cast

import pytest

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
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
    ingest_se_scraped_doc_html_map,
    load_se_bundle_from_archive,
    load_se_current_ir_from_archive,
    load_se_backfill_official_history_from_archive,
    load_se_official_act_from_archive,
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
    search_se_legacy_pdf_url,
    se_official_act_locator,
    se_official_base_ir_locator,
    se_official_clause_surface_locator,
    se_official_elaboration_locator,
    se_official_effects_plan_locator,
    se_official_ops_locator,
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
)
from lawvm.sweden.grafter import (
    apply_se_ops,
    canonicalize_se_table_section_text,
    extract_se_current_section_texts,
    materialize_se_statute_as_of,
    parse_se_statute,
    se_appendix_text_map,
    se_heading_before_section_map,
    se_section_text_map,
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

    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str:
        self.stored[locator] = data
        return "fakehash"

    def get(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    def get_latest(self, locator: str) -> bytes | None:
        return self.stored.get(locator)

    def has(self, locator: str, *, max_age_hours: float = float("inf")) -> bool:
        return locator in self.stored

    def is_fresh(self, locator: str, max_age_hours: float) -> bool:
        return locator in self.stored

    def locators(self, pattern: str = "%") -> list[str]:
        return [k for k in self.stored if pattern.replace("%", "") in k]


def test_fetch_se_official_artifacts_fetches_doc_and_pdf_and_stores_text(monkeypatch) -> None:
    doc_url = "https://svenskforfattningssamling.se/doc/2026286.html"
    pdf_url = "https://svenskforfattningssamling.se/sites/default/files/sfs/2026-03/SFS2026-286.pdf"
    archive = _FakeArchive(
        fetched={
            doc_url: b'<a href="/sites/default/files/sfs/2026-03/SFS2026-286.pdf">PDF</a>',
            pdf_url: b"%PDF-1.7 fake",
        }
    )
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")

    bundle = fetch_se_official_artifacts("2026:286", archive)

    assert bundle is not None
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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")
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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")

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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")

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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: None)

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

    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")
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
    monkeypatch.setattr("lawvm.sweden.fetch.time.sleep", lambda s: None)
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered legacy PDF text")
    monkeypatch.setattr("lawvm.sweden.fetch.search_se_legacy_pdf_url", lambda sfs_id: None)

    bundle = fetch_se_official_artifacts("2015:284", archive)

    assert bundle is not None
    assert bundle.doc_url == "https://rkrattsdb.gov.se/sfspdf/"
    assert bundle.pdf_url == legacy_pdf_url
    assert archive.stored[se_official_pdf_locator("2015:284")] == b"%PDF-1.7 legacy"


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
        lambda pdf_bytes: (
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
    assert load_se_official_clause_surface_from_archive(archive, "2026:286") is not None
    assert load_se_official_payload_surface_from_archive(archive, "2026:286") is not None
    assert load_se_official_elaboration_from_archive(archive, "2026:286") is not None
    assert load_se_official_effects_plan_from_archive(archive, "2026:286") is not None
    loaded = load_se_official_ops_from_archive(archive, "2026:286")
    assert loaded is not None
    assert loaded[0]["action"] == "replace"


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

    assert [op.action.value for op in ops] == ["text_replace", "text_replace"]
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
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Ny text."),
            source=OperationSource(statute_id="2026:999"),
        ),
        LegalOperation(
            op_id="appendix-replace-missing",
            sequence=6,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("appendix", "A"),)),
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
    assert adjudications[1].detail["target"] == "9"
    assert adjudications[3].source_statute == "2026:999"
    assert adjudications[3].detail["target_kind"] == "article"
    assert replayed.metadata["applied_op_count"] == 0


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
        action=StructuralAction.TEXT_REPLACE,
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


def test_check_se_official_replay_accepts_official_oracle_when_current_surface_drifts() -> None:
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
            "Senare driftad lydelse.\n"
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
    archive = _FakeArchive(
        stored={
            "se://sfs/2026:777/rk.current.json": json.dumps(base_payload, ensure_ascii=False).encode("utf-8"),
            "se://sfs/2026:286/official.act.json": json.dumps(official_act, ensure_ascii=False).encode("utf-8"),
        }
    )

    result = check_se_official_replay(archive, "2026:286")

    assert result["match_count"] == 1
    assert result["rows"][0]["classification"] == "official_oracle_match_current_surface_drift"


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
        check_se_official_replay(archive, "2026:290")
    except NotImplementedError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected NotImplementedError")

    assert "section:7a:preexisting_insert_target" in message


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
        check_se_official_replay(archive, "2018:1381", as_of="2018-08-01")
    except NotImplementedError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected NotImplementedError")

    assert "historical replay requires an older base surface or reverse patching" in message
    assert "section:16:preexisting_renumber_destination" in message
    assert "section:17:preexisting_insert_target" in message
    assert "section:18a:preexisting_insert_target" in message


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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")

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
    monkeypatch.setattr("lawvm.sweden.fetch.se_pdf_bytes_to_text", lambda pdf_bytes: "Recovered PDF text")

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
