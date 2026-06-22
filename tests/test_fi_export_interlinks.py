"""Tests for the neutral interlink projection exporter."""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from lawvm.core.inline_citation import InlineCitation, InlineCitationContext, InlineCitationKind
from lawvm.core.preparatory_reference import (
    PreparatoryReference,
    PreparatoryReferenceConfidence,
    PreparatoryReferenceKind,
)
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
)


class _Store:
    def read_oracle(self, _statute_id: str) -> bytes:
        return b"<akomaNtoso/>"


def test_get_statute_xml_accepts_canonical_viewer_id() -> None:
    from lawvm.tools.export_fi_interlinks import _get_statute_xml

    class _EngineOnlyStore:
        def __init__(self) -> None:
            self.reads: list[str] = []

        def read_oracle(self, statute_id: str) -> bytes | None:
            self.reads.append(statute_id)
            if statute_id == "2004/301":
                return b"<akomaNtoso/>"
            return None

    store = _EngineOnlyStore()
    assert _get_statute_xml("301/2004", store) == b"<akomaNtoso/>"
    assert store.reads == ["301/2004", "2004/301"]


def test_project_interlinks_for_statute_adapts_existing_fi_citation_families(
    monkeypatch,
) -> None:
    ref_module = types.ModuleType("lawvm.finland.ref_mention_extractor")
    prep_module = types.ModuleType("lawvm.finland.references.preparatory_reference_extractor")
    inline_module = types.ModuleType("lawvm.finland.references.inline_citation_extractor")

    ref_mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(statute_id="9/2023", section_label="2"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )
    prep_ref = PreparatoryReference(
        source_statute_id="711/2022",
        kind=PreparatoryReferenceKind.HE,
        canonical_id="he/2021/173",
        raw_text="HE 173/2021",
        committee_abbrev=None,
        he_year=2021,
        he_number=173,
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
    inline_citation = InlineCitation(
        source_doc_id="711/2022",
        source_doc_kind="statute",
        source_provision_ref="sec_4",
        kind=InlineCitationKind.STATUTE_INLINE,
        canonical_id="9/2023",
        raw_text="luonnonsuojelulain (9/2023)",
        case_year=2023,
        case_number=9,
        context=InlineCitationContext.ENACTED_STATUTE_BODY,
        source_span_file=None,
        source_span_byte_offset=None,
        source_span_byte_len=None,
    )

    monkeypatch.setattr(
        ref_module,
        "extract_all_reference_mentions",
        lambda _xml, _sid: SimpleNamespace(
            mentions=[ref_mention],
            diagnostics=[],
        ),
        raising=False,
    )
    monkeypatch.setattr(
        prep_module,
        "extract_preparatory_refs",
        lambda _xml, _sid: SimpleNamespace(
            refs=[prep_ref],
            rejected=[],
            lifecycle_observations=[],
        ),
        raising=False,
    )
    monkeypatch.setattr(
        inline_module,
        "extract_inline_citations",
        lambda *args, **kwargs: SimpleNamespace(
            citations=[inline_citation],
            pattern_matches=[],
        ),
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "lawvm.finland.ref_mention_extractor", ref_module)
    monkeypatch.setitem(sys.modules, "lawvm.finland.references.preparatory_reference_extractor", prep_module)
    monkeypatch.setitem(sys.modules, "lawvm.finland.references.inline_citation_extractor", inline_module)

    from lawvm.tools.export_fi_interlinks import _project_interlinks_for_statute

    projection = _project_interlinks_for_statute("711/2022", _Store())
    rows = list(projection.rows)

    assert projection.residuals == ()
    assert [row["interlink_id"] for row in rows] == [
        "fi.refs:711_2022:0",
        "fi.preparatory_refs:711_2022:0",
        "fi.inline_citations:711_2022:0",
    ]
    assert [row["surface_kind"] for row in rows] == [
        "xml_ref",
        "preparatory_ref",
        "prose_ref",
    ]
    assert rows[0]["target_work_id"] == "fi:normative_act:9/2023"
    assert rows[1]["target_work_kind"] == "government_proposal"
    assert rows[2]["target_local_id"] == "9/2023"


def test_export_interlinks_jsonl_writes_neutral_projection(
    monkeypatch,
    tmp_path,
) -> None:
    from lawvm.tools import export_fi_interlinks
    from lawvm.tools import _parallel_corpus

    monkeypatch.setattr(export_fi_interlinks, "_load_corpus_store", lambda: object())
    monkeypatch.setattr(
        _parallel_corpus,
        "project_corpus_parallel",
        lambda **_kwargs: ([
            {
                "interlink_id": "x",
                "source_jurisdiction": "fi",
                "source_work_kind": "normative_act",
                "source_local_id": "711/2022",
                "source_work_id": "fi:normative_act:711/2022",
                "source_locator": None,
                "surface_text": "surface",
                "surface_kind": "prose_ref",
                "role": "cites",
                "target_jurisdiction": "fi",
                "target_work_kind": "normative_act",
                "target_local_id": "9/2023",
                "target_work_id": "fi:normative_act:9/2023",
                "target_locator": None,
                "target_url": None,
                "candidate_work_ids": None,
                "resolution_status": "resolved",
                "confidence": "exact",
                "resolver_id": "test",
                "source_artifact_id": None,
                "source_span_byte_offset": None,
                "source_span_byte_len": None,
                "rendered_statute_id": None,
                "rendered_effective_date": None,
                "rendered_address": None,
                "rendered_segment_index": None,
                "rendered_char_start": None,
                "rendered_char_end": None,
                "valid_at_start": None,
                "valid_at_end": None,
                "detail_json": "{}",
            }
        ], []),
    )

    count = export_fi_interlinks.export_fi_interlinks(
        [(1, "711/2022")],
        data_dir=str(tmp_path),
        use_parquet=False,
        workers=1,
    )

    assert count == 1
    assert (tmp_path / "lawvm_interlinks.jsonl").read_text(encoding="utf-8").count("\n") == 1
