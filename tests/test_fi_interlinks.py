from __future__ import annotations

import json

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


def test_fi_reference_mention_adapter_parses_and_preserves_akn_locator() -> None:
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="1996/1093", section_label="18"),
        target_provision_ref=ProvisionRef(
            statute_id="1889/39-001",
            provision_path="chp_10__sec_2",
        ),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )

    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(
            mention,
            interlink_id="ref-akn-locator",
            surface_text="(39/1889) 10 luvun 2 §:ssä",
        )
    )

    assert row["target_locator"] == "chapter:10/section:2"
    assert json.loads(str(row["detail_json"])) == {
        "target_locator_resolver": "fi.akn_eid",
        "target_raw_locator": "chp_10__sec_2",
    }


def test_fi_reference_mention_adapter_parses_chapter_only_akn_locator() -> None:
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="1996/1093", section_label="18"),
        target_provision_ref=ProvisionRef(
            statute_id="1889/39-001",
            provision_path="chp_10",
        ),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )

    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(
            mention,
            interlink_id="ref-akn-chapter",
            surface_text="rikoslain 10 luvussa",
        )
    )

    assert row["target_locator"] == "chapter:10"
    assert json.loads(str(row["detail_json"])) == {
        "target_locator_resolver": "fi.akn_eid",
        "target_raw_locator": "chp_10",
    }


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


def test_fi_ambiguous_eu_nickname_surfaces_disambiguation_candidates() -> None:
    """An ambiguous EU-by-nickname cite with a SMALL discrete candidate CELEX set
    is surfaced as a one-of-K disambiguation link (candidate_work_ids), not dropped.

    ``jätedirektiivi`` is a genuinely-ambiguous nickname: the registry maps it to
    two CELEX ids and refuses to pick one. The flat mention carries only the
    ``eu-nickname:<surface>`` placeholder; the consumer recovers the small
    discrete candidate set and emits it as an AMBIGUOUS/HEURISTIC possibility set
    (never a resolved single-target EXACT link).
    """
    from lawvm.finland.references.registries import eu_nickname

    result = eu_nickname.lookup("jätedirektiivi")
    assert result.registry_status is eu_nickname.RegistryStatus.MULTIPLE
    assert 2 <= len(result.candidates) <= 4
    expected = "|".join(f"celex:{c}" for c in result.candidates)

    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(
            statute_id="eu-nickname:jätedirektiivi", section_label="3"
        ),
        cite_kind=CiteKind.EU,
        cite_confidence=CiteConfidence.AMBIGUOUS,
        phrase_lemma="eu_directive_nickname_article",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text="jätedirektiivin 3 artiklassa",
    )
    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(mention, interlink_id="amb-eu-1")
    )
    # One-of-K disambiguation link: the whole candidate set, no single pick.
    assert row["candidate_work_ids"] == expected
    assert row["resolution_status"] == "ambiguous"
    # A POSSIBILITY set, never a definite EXACT single-target link.
    assert row["confidence"] == "heuristic"
    assert row["target_work_id"] is None
    # The cited-article locator and surface are preserved for rendering.
    assert row["target_locator"] == "section:3"
    assert row["surface_text"] == "jätedirektiivin 3 artiklassa"


def test_fi_ambiguous_unknown_nickname_stays_unlinked() -> None:
    """An AMBIGUOUS cite whose nickname the registry does NOT know (no candidate
    set) is left exactly as before — no disambiguation candidates are invented."""
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(
            statute_id="eu-nickname:tuntematonasetusxyz", section_label="3"
        ),
        cite_kind=CiteKind.EU,
        cite_confidence=CiteConfidence.AMBIGUOUS,
        phrase_lemma="eu_directive_nickname_article",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text="tuntematon 3 artiklassa",
    )
    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(mention, interlink_id="amb-eu-2")
    )
    assert row["candidate_work_ids"] is None
    assert row["resolution_status"] == "ambiguous"


def test_fi_resolved_single_target_carries_no_disambiguation_candidates() -> None:
    """A RESOLVED (EXACT single-target) cite is byte-unchanged: no candidate set,
    a concrete target, EXACT confidence — the disambiguation lane never fires."""
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=ProvisionRef(statute_id="9/2023", section_label="2"),
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.EXACT,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
    )
    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(mention, interlink_id="res-nocand")
    )
    assert row["candidate_work_ids"] is None
    assert row["resolution_status"] == "resolved"
    assert row["confidence"] == "exact"
    assert row["target_work_id"] == "fi:normative_act:9/2023"


def test_fi_open_vague_reference_stays_unlinked_with_no_candidates() -> None:
    """An OPEN (vague ``muualla laissa``) reference names no target and no
    candidate set — it stays unlinked exactly as before."""
    mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="4"),
        target_provision_ref=None,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.OPEN,
        phrase_lemma="in_prose_fi",
        source_span=None,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text="muualla laissa",
    )
    row = legal_interlink_to_row(
        fi_interlink_from_reference_mention(mention, interlink_id="open-nocand")
    )
    assert row["candidate_work_ids"] is None
    assert row["resolution_status"] == "unresolved"
    assert row["target_work_id"] is None


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
