from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from lxml import etree as ET

from lawvm.uk_legislation.effects import UKEffectRecord
from scripts import uk_applied_effect_current_review as review


def _effect(*, effect_id: str = "eff-1", effect_type: str = "inserted") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id=effect_id,
        effect_type=effect_type,
        applied=True,
        requires_applied=False,
        modified="2021-01-01",
        affected_uri="/id/ukpga/2020/1/section/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2020",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="https://www.legislation.gov.uk/id/ukpga/2021/2",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2021",
        affecting_number="2",
        affecting_provisions="s. 4",
        affecting_title="Test Act 2021",
        in_force_dates=[{"date": "2021-01-01", "prospective": "false"}],
    )


class _Archive:
    def __init__(self, current_xml: bytes | None = None) -> None:
        self.current_xml = current_xml

    def get(self, locator: str) -> bytes | None:
        if locator == "https://www.legislation.gov.uk/ukpga/2020/1/data.xml":
            return self.current_xml
        return b"<Legislation>affecting source</Legislation>"


def _patch_effect_source(monkeypatch, effect: UKEffectRecord, source_text: str) -> None:
    monkeypatch.setattr(
        review,
        "load_effects_for_statute_from_archive",
        lambda statute_id, archive: [effect],
    )
    source_el = ET.fromstring(f"<P1>{source_text}</P1>".encode())
    monkeypatch.setattr(
        review,
        "select_source_for_effect",
        lambda **kwargs: SimpleNamespace(
            extracted_el=source_el,
            source_context=SimpleNamespace(
                xml_bytes=b"<Legislation>source bytes</Legislation>",
                locator="https://www.legislation.gov.uk/ukpga/2021/2/data.xml",
            ),
        ),
    )


def test_applied_effect_review_detects_current_expected_phrase(
    monkeypatch,
) -> None:
    effect = _effect()
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 insert "new public review phrase".',
    )
    archive = _Archive(
        b"<Legislation><Text>new public review phrase</Text>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "current_xml_has_expected_marker"
    assert rows[0].expected_phrase_role == "postimage"
    assert rows[0].current_xml_has_expected_phrase is True
    assert rows[0].agreement_residual["status"] == "frontier"
    assert rows[0].agreement_residual["forbidden_shortcuts"] == [
        "applied_effect_as_official_error",
        "source_fragment_as_payload_authority",
        "current_xml_absence_as_replay_authorization",
        "review_lead_as_automatic_consolidation_change",
    ]


def test_applied_effect_review_surfaces_no_marker_public_review_candidate(
    monkeypatch,
) -> None:
    effect = _effect(effect_id="key-visible-gap")
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 insert "phrase absent from current xml".',
    )
    archive = _Archive(
        b"<Legislation><Text>old text only</Text>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )
    payload = json.loads(review._emit_json(rows))

    assert rows[0].review_status == "needs_public_review_no_obvious_current_marker"
    assert rows[0].expected_phrase_role == "postimage"
    assert rows[0].agreement_residual["status"] == "residual"
    assert rows[0].agreement_residual["missing_proofs"] == [
        "public_page_review",
        "page_declared_current_timeline_xml",
        "savings_extent_or_editorial_policy_review",
    ]
    assert rows[0].public_current_urls == (
        "https://www.legislation.gov.uk/ukpga/2020/1/section/1",
    )
    assert rows[0].current_surface_preview == "old text only" + (
        " current context" * 20
    )
    assert "old text only" in rows[0].current_expected_phrase_context
    assert payload["truth_claim"] == "applied_effect_current_review_not_replay_authority"
    assert payload["replay_claims"] is False
    assert payload["summary"]["candidate_public_review_count"] == 1


def test_unrelated_commentary_does_not_refute_effect_candidate(monkeypatch) -> None:
    effect = _effect(effect_id="key-specific-effect")
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 insert "specific absent phrase".',
    )
    archive = _Archive(
        b'<Legislation><Commentary Type="F">Different amendment note</Commentary>'
        b"<Text>old text only</Text>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "needs_public_review_no_obvious_current_marker"
    assert rows[0].current_xml_has_any_commentary_marker is False


def test_omitted_phrase_absence_is_not_public_review_candidate(monkeypatch) -> None:
    effect = _effect(effect_id="key-omission", effect_type="words omitted")
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 omit "removed phrase".',
    )
    archive = _Archive(
        b"<Legislation><Text>old text only</Text>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )

    assert rows[0].expected_phrase == "removed phrase"
    assert rows[0].expected_phrase_role == "removed_preimage"
    assert rows[0].review_status == "current_xml_lacks_removed_phrase"
    assert rows[0].agreement_residual["status"] == "frontier"


def test_omitted_phrase_outside_affected_provision_does_not_create_candidate(
    monkeypatch,
) -> None:
    effect = _effect(effect_id="key-omission", effect_type="words omitted")
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 omit "removed phrase".',
    )
    archive = _Archive(
        b"<Legislation>"
        b"<LongTitle>removed phrase appears in non-target title</LongTitle>"
        b"<P1 eId=\"section-1\"><Text>target text no removed words</Text></P1>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "current_xml_lacks_removed_phrase"
    assert rows[0].current_review_surface == "affected_provision"
    assert rows[0].current_review_surface_locator == "section-1"
    assert rows[0].current_xml_has_expected_phrase is False
    assert rows[0].agreement_residual["detail"]["current_review_surface"] == (
        "affected_provision"
    )


def test_omitted_phrase_still_present_is_public_review_candidate(monkeypatch) -> None:
    effect = _effect(effect_id="key-omission", effect_type="words omitted")
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 omit "retained phrase".',
    )
    archive = _Archive(
        b"<Legislation><Text>retained phrase</Text>"
        + (b" current context" * 20)
        + b"</Legislation>"
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=archive,
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "needs_public_review_removed_phrase_still_present"
    assert rows[0].agreement_residual["status"] == "residual"


def test_removed_phrase_extraction_uses_omitted_entry_not_heading() -> None:
    source = (
        "under the heading “industry, business, finance etc” omit the entry for "
        "the Office of Tax Simplification."
    )

    assert review._expected_phrase(source, effect_type="entry omitted") == (
        "the Office of Tax Simplification",
        "removed_preimage",
    )


def test_removed_phrase_extraction_uses_phrase_inside_definition() -> None:
    source = (
        "In the definition of “community care services”, in paragraph (a) omit "
        "“England or”."
    )

    assert review._expected_phrase(source, effect_type="words omitted") == (
        "England or",
        "removed_preimage",
    )


def test_removed_phrase_extraction_keeps_omitted_definition_label() -> None:
    source = 'In section 272(general interpretation), omit the definition of “scheme administrator”.'

    assert review._expected_phrase(source, effect_type="words omitted") == (
        "scheme administrator",
        "removed_preimage",
    )


def test_removed_phrase_extraction_drops_unreviewable_label_contexts() -> None:
    assert review._expected_phrase(
        'in the definition of “primary legislation” omit paragraph (b).',
        effect_type="words omitted",
    ) == ("", "")
    assert review._expected_phrase(
        'omit the words following the definition of “prescribed charity”;',
        effect_type="words omitted",
    ) == ("", "")
    assert review._expected_phrase(
        'the word “and” at the end of the definition of “fire and rescue authority”,',
        effect_type="word omitted",
    ) == ("", "")
    assert review._expected_phrase(
        'Under the heading “C. Insects and mites”, omit entry 62.',
        effect_type="words omitted",
    ) == ("", "")


def test_current_surface_combines_named_sibling_subunits(monkeypatch) -> None:
    effect = _effect(effect_type="substituted for words")
    effect = UKEffectRecord(
        **{
            **effect.__dict__,
            "affected_provisions": "s. 1(1)(a) (b)",
        }
    )
    _patch_effect_source(
        monkeypatch,
        effect,
        "In subsection (1), for paragraphs (a) and (b) substitute— alpha beta.",
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation>"
            b"<P3 eId=\"section-1-1-a\">alpha</P3>"
            b"<P3 eId=\"section-1-1-b\">beta</P3>"
            b"<P2 eId=\"section-1-1\">parent fallback</P2>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "current_xml_has_expected_marker"
    assert rows[0].current_review_surface_locator == "section-1-1-a section-1-1-b"
    assert rows[0].current_xml_has_expected_phrase is True


def test_single_quoted_substitution_preimage_is_not_treated_as_postimage(
    monkeypatch,
) -> None:
    effect = _effect(effect_type="substituted for words")
    _patch_effect_source(
        monkeypatch,
        effect,
        "For the words from “old words” onwards substitute new paragraphs.",
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><Text>old words</Text>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        today=date(2026, 6, 5),
    )

    assert rows == []


def test_dashed_substitution_postimage_can_be_reviewed(monkeypatch) -> None:
    effect = _effect(effect_type="substituted")
    _patch_effect_source(
        monkeypatch,
        effect,
        "For section 1 substitute— new substituted provision text.",
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><Text>old text</Text>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        today=date(2026, 6, 5),
    )

    assert rows[0].expected_phrase == "new substituted provision text"
    assert rows[0].expected_phrase_role == "postimage"
    assert rows[0].review_status == "needs_public_review_no_obvious_current_marker"


def test_applied_effect_review_keeps_missing_current_xml_as_frontier(
    monkeypatch,
) -> None:
    effect = _effect()
    _patch_effect_source(
        monkeypatch,
        effect,
        'In section 1 insert "missing current xml phrase".',
    )

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(None),
        today=date(2026, 6, 5),
    )

    assert rows[0].review_status == "current_xml_unavailable_frontier"
    assert rows[0].agreement_residual["family"] == "source_footing_gap"
    assert rows[0].agreement_residual["status"] == "frontier"


def test_filter_rows_limits_after_status_selection(monkeypatch) -> None:
    effect = _effect(effect_id="eff-marker")
    _patch_effect_source(monkeypatch, effect, 'In section 1 insert "present phrase".')
    marker_rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><Text>present phrase</Text>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        today=date(2026, 6, 5),
    )

    effect_gap = _effect(effect_id="eff-gap")
    _patch_effect_source(monkeypatch, effect_gap, 'In section 1 insert "absent phrase".')
    gap_rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><Text>old text only</Text>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        today=date(2026, 6, 5),
    )

    filtered = review._filter_rows(
        [*marker_rows, *gap_rows],
        statuses=["needs_public_review_no_obvious_current_marker"],
        limit=1,
    )

    assert [row.effect_id for row in filtered] == ["eff-gap"]


def test_removed_phrase_status_prefilter_skips_non_removal_source_extraction(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        review,
        "load_effects_for_statute_from_archive",
        lambda statute_id, archive: [_effect(effect_type="inserted")],
    )

    def _unexpected_source_selection(**kwargs):
        raise AssertionError("source extraction should be prefiltered")

    monkeypatch.setattr(review, "select_source_for_effect", _unexpected_source_selection)

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><P1 eId=\"section-1\">target text</P1>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        include_statuses=["needs_public_review_removed_phrase_still_present"],
        today=date(2026, 6, 5),
    )

    assert rows == []


def test_removed_phrase_status_prefilter_skips_heading_targets(monkeypatch) -> None:
    effect = _effect(effect_type="words omitted")
    effect = UKEffectRecord(
        **{
            **effect.__dict__,
            "affected_provisions": "Sch. 7 para. 22 heading",
        }
    )
    monkeypatch.setattr(
        review,
        "load_effects_for_statute_from_archive",
        lambda statute_id, archive: [effect],
    )

    def _unexpected_source_selection(**kwargs):
        raise AssertionError("heading facet should be prefiltered")

    monkeypatch.setattr(review, "select_source_for_effect", _unexpected_source_selection)

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><P1 eId=\"section-1\">removed phrase in body</P1>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        include_statuses=["needs_public_review_removed_phrase_still_present"],
        today=date(2026, 6, 5),
    )

    assert rows == []


def test_removed_phrase_status_prefilter_skips_non_act_whole_xml_fallback(
    monkeypatch,
) -> None:
    effect = _effect(effect_type="words omitted")
    effect = UKEffectRecord(
        **{
            **effect.__dict__,
            "affected_provisions": "Annex 5 Pt. C Final Table",
        }
    )
    monkeypatch.setattr(
        review,
        "load_effects_for_statute_from_archive",
        lambda statute_id, archive: [effect],
    )

    def _unexpected_source_selection(**kwargs):
        raise AssertionError("unresolved non-Act target should be prefiltered")

    monkeypatch.setattr(review, "select_source_for_effect", _unexpected_source_selection)

    rows = review.build_review_rows(
        ["ukpga/2020/1"],
        archive=_Archive(
            b"<Legislation><P1 eId=\"section-9\">removed phrase elsewhere</P1>"
            + (b" current context" * 20)
            + b"</Legislation>"
        ),
        include_statuses=["needs_public_review_removed_phrase_still_present"],
        today=date(2026, 6, 5),
    )

    assert rows == []


def test_target_url_parser_preserves_alphanumeric_labels_and_ranges() -> None:
    assert review._affected_provision_eid_candidates("Art. 2A-2C") == (
        "article-2A",
        "article-2B",
        "article-2C",
    )
    assert review._affected_provision_eid_candidates("inserted s. 31A") == (
        "section-31A",
    )
    assert review._affected_provision_eid_candidates("s. 44(8) (9)")[:4] == (
        "section-44-8-9",
        "section-44-8",
        "section-44-9",
        "section-44",
    )
    assert review._affected_provision_eid_candidates("Sch. 7 para. 10(1A)(a)")[:3] == (
        "schedule-7-paragraph-10-1A-a",
        "schedule-7-paragraph-10-1A",
        "schedule-7-paragraph-10-a",
    )
