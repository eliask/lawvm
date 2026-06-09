from __future__ import annotations

from types import SimpleNamespace

from lawvm.core.preparatory_reference import (
    PreparatoryReferenceConfidence,
    PreparatoryReferenceKind,
)
from lawvm.finland.preparatory_reference_extractor import PrepRefExtractionResult
from lawvm.tools import provenance


def _payload(
    *,
    effective: str = "2026-06-01",
    source_amendment: str | None = "2026/269",
    content_state: str = "live",
) -> dict:
    source = {"statute_id": source_amendment} if source_amendment else {}
    return {
        "status": "selected",
        "text": {"rendered": "3 § Test wording", "available": True},
        "version": {
            "effective": effective,
            "enacted": "2026-05-01",
            "content_state": content_state,
            "expires": "",
            "variant_kind": "text",
            "applicability": None,
        },
        "source": source,
        "title": "Test statute",
    }


def _ref(kind: PreparatoryReferenceKind, canonical_id: str, raw_text: str) -> SimpleNamespace:
    he_year = 2025 if kind == PreparatoryReferenceKind.HE else None
    he_number = 188 if kind == PreparatoryReferenceKind.HE else None
    return SimpleNamespace(
        kind=kind,
        canonical_id=canonical_id,
        raw_text=raw_text,
        he_year=he_year,
        he_number=he_number,
        confidence=PreparatoryReferenceConfidence.EXACT,
    )


class _FakeCorpusStore:
    def __init__(self) -> None:
        self.closed = False

    def read_source(self, statute_id: str) -> bytes:
        assert statute_id == "2026/269"
        return b"<xml/>"

    def close(self) -> None:
        self.closed = True


def _patch_replay(monkeypatch, payload: dict) -> None:
    def fake_resolve_provision_state(**kwargs):
        assert kwargs["statute_id"] == "2011/805"
        assert kwargs["jurisdiction"] == "fi"
        assert kwargs["provision"] == "chapter:3/section:1"
        assert kwargs["query_type"] == "in_force"
        return payload

    monkeypatch.setattr(provenance, "resolve_provision_state", fake_resolve_provision_state)


def _patch_refs(monkeypatch) -> None:
    monkeypatch.setattr(provenance, "get_corpus_store", _FakeCorpusStore)

    def fake_extract_preparatory_refs(xml: bytes, statute_id: str) -> PrepRefExtractionResult:
        assert xml == b"<xml/>"
        assert statute_id == "2026/269"
        return PrepRefExtractionResult(
            refs=[
                _ref(PreparatoryReferenceKind.HE, "he/2025/188", "HE 188/2025"),
                _ref(PreparatoryReferenceKind.COMMITTEE_REPORT, "fi.committee.lavm.3.2026", "LaVM 3/2026"),
                _ref(PreparatoryReferenceKind.PARLIAMENT_RESPONSE, "fi.ev.23.2026", "EV 23/2026"),
            ]
        )

    monkeypatch.setattr(provenance, "extract_preparatory_refs", fake_extract_preparatory_refs)


def _patch_he_meta(monkeypatch, finlex_state: str = "enacted") -> None:
    def fake_lookup_he_meta(he_id: str, data_dir: str) -> dict:
        assert he_id == "he/2025/188"
        assert data_dir == "test-data"
        return {
            "he_id": he_id,
            "title": "HE title",
            "ministry": "OM",
            "date_issued": "2025-11-20",
            "finlex_state": finlex_state,
        }

    monkeypatch.setattr(provenance, "_lookup_he_meta", fake_lookup_he_meta)


def test_build_provenance_full_chain_populates_originating_he(monkeypatch) -> None:
    _patch_replay(monkeypatch, _payload())
    _patch_refs(monkeypatch)
    _patch_he_meta(monkeypatch, finlex_state="enacted")

    record = provenance.build_provenance(
        "2011/805", "§3:1", "2026-06-09", data_dir="test-data"
    )

    assert record["schema"] == "lawvm.provenance.v1"
    assert record["locator"] == "chapter:3/section:1"
    assert record["in_force"]["source_amendment"] == "2026/269"
    assert record["originating_he"] == {
        "he_id": "he/2025/188",
        "title": "HE title",
        "ministry": "OM",
        "date_issued": "2025-11-20",
        "finlex_state": "enacted",
        "enacted_law_surfaced": "2026/269",
        "confidence": "exact",
    }
    assert record["preparatory"] == [
        {"kind": "he", "canonical_id": "he/2025/188", "raw_text": "HE 188/2025"},
        {
            "kind": "committee_report",
            "canonical_id": "fi.committee.lavm.3.2026",
            "raw_text": "LaVM 3/2026",
        },
        {"kind": "parliament_response", "canonical_id": "fi.ev.23.2026", "raw_text": "EV 23/2026"},
    ]


def test_build_provenance_pending_he_state_emits_gap_note(monkeypatch) -> None:
    _patch_replay(monkeypatch, _payload())
    _patch_refs(monkeypatch)
    _patch_he_meta(monkeypatch, finlex_state="pending")

    record = provenance.build_provenance(
        "2011/805", "§3:1", "2026-06-09", data_dir="test-data"
    )

    assert record["originating_he"]["finlex_state"] == "pending"
    assert any(
        "HE corpus finlex_state=pending, but this HE enacted L 2026/269 (in force 2026-06-01); "
        "surfaced by amendment→HE inversion, not the HE-corpus projection."
        == note
        for note in record["notes"]
    )


def test_build_provenance_base_statute_has_no_originating_he(monkeypatch) -> None:
    _patch_replay(monkeypatch, _payload(effective="", source_amendment=None))
    monkeypatch.setattr(provenance, "_lookup_he_meta", lambda he_id, data_dir: None)

    record = provenance.build_provenance(
        "2011/805", "§3:1", "2026-06-09", data_dir="test-data"
    )

    assert record["originating_he"] is None
    assert record["preparatory"] == []
    assert record["commencement"]["gate"] == "base"
    assert any("base statute text" in note for note in record["notes"])


def test_build_provenance_future_commencement_gate(monkeypatch) -> None:
    _patch_replay(monkeypatch, _payload(effective="2026-07-01"))
    _patch_refs(monkeypatch)
    _patch_he_meta(monkeypatch, finlex_state="enacted")

    record = provenance.build_provenance(
        "2011/805", "§3:1", "2026-06-09", data_dir="test-data"
    )

    assert record["commencement"] == {
        "effective": "2026-07-01",
        "enacted": "2026-05-01",
        "content_state": "live",
        "gate": "future",
    }
