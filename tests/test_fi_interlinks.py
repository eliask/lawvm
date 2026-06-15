from __future__ import annotations

from lawvm.core.inline_citation import InlineCitation, InlineCitationContext, InlineCitationKind
from lawvm.core.interlinks import legal_interlink_to_row
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
    SourceSpan,
)
from lawvm.finland.interlinks import (
    fi_interlink_from_inline_citation,
    fi_interlink_from_preparatory_reference,
    fi_interlink_from_reference_mention,
)


def test_fi_reference_mention_adapter_preserves_role_status_and_locator() -> None:
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(statute_id="9/2023", section_label="2", subsection_num=3),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=SourceSpan(source_file="fixture.xml", byte_offset=10, byte_len=20),
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )
    link = fi_interlink_from_reference_mention(
        mention,
        interlink_id="ref-1",
        surface_text="luonnonsuojelulain 2 §",
    )
    row = legal_interlink_to_row(link)
    assert row["surface_kind"] == "xml_ref"
    assert row["role"] == "cites"
    assert row["resolution_status"] == "resolved"
    assert row["source_locator"] == "section:4"
    assert row["target_locator"] == "section:2/subsection:3"
    assert row["source_span_byte_offset"] == 10


def test_fi_reference_mention_adapter_uses_owned_surface_text() -> None:
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(statute_id="9/2023"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
        surface_text="luonnonsuojelulain (9/2023)",
    )
    row = legal_interlink_to_row(fi_interlink_from_reference_mention(mention, interlink_id="ref-surface"))
    assert row["surface_text"] == "luonnonsuojelulain (9/2023)"


def test_fi_reference_mention_adapter_resolves_internal_target_contextually() -> None:
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(statute_id="", section_label="7"),
        cite_kind=CiteKind.INTERNAL,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )
    link = fi_interlink_from_reference_mention(mention, interlink_id="ref-internal")
    row = legal_interlink_to_row(link)
    assert row["resolution_status"] == "resolved"
    assert row["target_work_id"] == "fi:normative_act:711/2022"
    assert row["target_locator"] == "section:7"


def test_fi_inline_citation_adapter_maps_statute_target() -> None:
    citation = InlineCitation(
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
    link = fi_interlink_from_inline_citation(citation, interlink_id="inline-1")
    row = legal_interlink_to_row(link)
    assert row["source_work_kind"] == "normative_act"
    assert row["target_work_kind"] == "normative_act"
    assert row["target_local_id"] == "9/2023"
    assert row["surface_text"] == "luonnonsuojelulain (9/2023)"


def test_fi_preparatory_reference_adapter_maps_history_role() -> None:
    ref = PreparatoryReference(
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
    link = fi_interlink_from_preparatory_reference(ref, interlink_id="prep-1")
    row = legal_interlink_to_row(link)
    assert row["role"] == "preparatory_history"
    assert row["surface_kind"] == "preparatory_ref"
    assert row["target_work_kind"] == "government_proposal"
    assert row["target_local_id"] == "he/2021/173"
