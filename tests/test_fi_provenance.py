from __future__ import annotations

from lawvm.core.preparatory_reference import (
    PreparatoryReference,
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


def _ref(kind: PreparatoryReferenceKind, canonical_id: str, raw_text: str) -> PreparatoryReference:
    he_year = 2025 if kind == PreparatoryReferenceKind.HE else None
    he_number = 188 if kind == PreparatoryReferenceKind.HE else None
    return PreparatoryReference(
        source_statute_id="2026/269",
        kind=kind,
        canonical_id=canonical_id,
        raw_text=raw_text,
        committee_abbrev=None,
        he_year=he_year,
        he_number=he_number,
        eu_form=None,
        eu_number=None,
        eu_year=None,
        celex=None,
        oj_series=None,
        oj_number=None,
        oj_date=None,
        oj_page=None,
        confidence=PreparatoryReferenceConfidence.EXACT,
        source_span_file=None,
        source_span_byte_offset=None,
        source_span_byte_len=None,
        valid_at_interval=(None, None),
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


# ---------------------------------------------------------------------------
# Statute-level provenance (Q3): HE -> [enacted amendments] inversion
# ---------------------------------------------------------------------------


class _OpSource:
    def __init__(self, statute_id, enacted, effective, legal_status, title):
        self.statute_id = statute_id
        self.enacted = enacted
        self.effective = effective
        self.legal_status = legal_status
        self.title = title


def _patch_statute_replay(monkeypatch) -> None:
    src = _OpSource("2026/269", "2026-04-17", "2026-06-01", "commenced", "Laki ... muuttamisesta")
    monkeypatch.setattr(
        provenance,
        "_amendment_sources_from_replay",
        lambda statute_id: {"2026/269": src},
    )


def _patch_amendment_children(monkeypatch) -> None:
    import lawvm.finland.amendment_index as ai

    # Two amendments; one we have source XML for, one we don't.
    monkeypatch.setattr(
        ai, "get_amendment_children", lambda: {"2011/805": ["2026/269", "2014/672"]}
    )


class _StatuteFakeStore:
    def __init__(self):
        self.closed = False

    def read_source(self, statute_id):
        if statute_id == "2026/269":
            return b"<xml/>"
        return None  # 2014/672: source unavailable

    def close(self):
        self.closed = True


def test_build_statute_provenance_inverts_he_to_amendments(monkeypatch) -> None:
    _patch_statute_replay(monkeypatch)
    _patch_amendment_children(monkeypatch)
    _patch_refs(monkeypatch)  # extractor returns HE/cmte/EV for 2026/269
    # Override the store _patch_refs installed with the statute-level fake (which
    # returns None for 2014/672 to exercise the missing-source path).
    monkeypatch.setattr(provenance, "get_corpus_store", _StatuteFakeStore)
    _patch_he_meta(monkeypatch, finlex_state="pending")

    record = provenance.build_statute_provenance(
        "2011/805", as_of="2026-06-09", data_dir="test-data"
    )

    assert record["schema"] == "lawvm.provenance_statute.v1"
    assert record["statute_id"] == "2011/805"
    assert record["amendment_count"] == 2
    assert record["he_resolved_count"] == 1

    by_id = {a["amendment_id"]: a for a in record["amendments"]}
    a269 = by_id["2026/269"]
    assert a269["applied_in_replay"] is True
    assert a269["commencement"]["effective"] == "2026-06-01"
    assert a269["commencement"]["enacted"] == "2026-04-17"
    assert a269["originating_he"]["he_id"] == "he/2025/188"
    # finlex_state pending but still surfaced as an enacted amendment (inversion)
    assert a269["originating_he"]["finlex_state"] == "pending"
    assert a269["committee_refs"][0]["raw_text"] == "LaVM 3/2026"
    assert a269["parliament_response_refs"][0]["raw_text"] == "EV 23/2026"

    a672 = by_id["2014/672"]
    assert a672["preparatory_available"] is False
    assert a672["originating_he"] is None
    # ordering: newest amendment first
    assert record["amendments"][0]["amendment_id"] == "2026/269"


def test_statute_provenance_human_render(monkeypatch) -> None:
    _patch_statute_replay(monkeypatch)
    _patch_amendment_children(monkeypatch)
    _patch_refs(monkeypatch)
    monkeypatch.setattr(provenance, "get_corpus_store", _StatuteFakeStore)
    _patch_he_meta(monkeypatch, finlex_state="pending")

    record = provenance.build_statute_provenance(
        "2011/805", as_of="2026-06-09", data_dir="test-data"
    )
    text = provenance._render_statute_human(record)
    assert "statute provenance" in text
    assert "L 2026/269" in text
    assert "in force 2026-06-01" in text
    assert "HE   : he/2025/188" in text
    assert "EV 23/2026" in text
