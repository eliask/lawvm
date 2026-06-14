"""Tests for the UK effect-feed acquisition-frontier classifier.

These are synthetic/fixture tests: they assert the typed state taxonomy, the
totality of the classifier, the base-metadata-only promotion gate, and the
determinism of the report builder, without relying on fragile corpus literals.
"""

from __future__ import annotations

import dataclasses

import pytest

from lawvm.uk_legislation.acquisition_frontier import (
    UKAcquisitionFrontierState,
    UKEffectFeedPageState,
    UKEffectFeedState,
    classify_uk_acquisition_frontier,
    classify_uk_effect_feed_blob,
    uk_acquisition_frontier_states_to_report,
)

# A minimal well-formed Atom effect feed page with one ukm:Effect entry.
_NONEMPTY_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">
  <entry>
    <id>http://www.legislation.gov.uk/changes/effect/eff-1</id>
    <ukm:EffectsList>
      <ukm:Effect EffectId="eff-1" Type="words substituted" Applied="true"/>
    </ukm:EffectsList>
  </entry>
</feed>"""

# A well-formed Atom feed page that published no entries at all.
_EMPTY_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:openSearch="http://a9.com/-/spec/opensearch/1.1/">
  <openSearch:totalResults>0</openSearch:totalResults>
</feed>"""

_MULTIPLE_CHOICES_FEED = b"""<html><body>
  <h1>Multiple Choices</h1>
  <p>The link that you've followed could mean either of the following:</p>
  <a href="/ukpga/Eliz2/3-4/18/enacted">Army Act 1955 (repealed)</a>
</body></html>"""

_HTTP_404_FEED = b"HTTP 404 Not Found\n\nThe requested resource was not found."

_UNPARSEABLE_FEED = b"<feed xmlns='http://www.w3.org/2005/Atom'><entry>"


def test_blob_classifier_present_nonempty() -> None:
    page = classify_uk_effect_feed_blob(_NONEMPTY_FEED)
    assert page.state is UKEffectFeedState.FEED_PRESENT_NONEMPTY
    assert page.entry_count == 1
    assert page.state.is_acquisition_frontier is False


def test_blob_classifier_empty_feed() -> None:
    page = classify_uk_effect_feed_blob(_EMPTY_FEED)
    assert page.state is UKEffectFeedState.FEED_EMPTY
    assert page.entry_count == 0
    assert page.state.is_acquisition_frontier is True


def test_blob_classifier_absent_for_none_and_empty_bytes() -> None:
    assert classify_uk_effect_feed_blob(None).state is UKEffectFeedState.FEED_PAGE_ABSENT
    assert classify_uk_effect_feed_blob(b"").state is UKEffectFeedState.FEED_PAGE_ABSENT


def test_blob_classifier_http_404_banner() -> None:
    page = classify_uk_effect_feed_blob(_HTTP_404_FEED)
    assert page.state is UKEffectFeedState.FEED_HTTP_404


def test_blob_classifier_multiple_choices() -> None:
    page = classify_uk_effect_feed_blob(_MULTIPLE_CHOICES_FEED)
    assert page.state is UKEffectFeedState.FEED_MULTIPLE_CHOICES


def test_blob_classifier_unparseable_records_parse_error() -> None:
    page = classify_uk_effect_feed_blob(_UNPARSEABLE_FEED)
    assert page.state is UKEffectFeedState.FEED_UNPARSEABLE
    assert page.parse_error


def test_classifier_is_total_over_every_state() -> None:
    # Every taxonomy value is reachable: prove the map is surjective onto the
    # frontier classes plus the present-nonempty class via blob+base inputs.
    seen: set[UKEffectFeedState] = set()
    seen.add(classify_uk_acquisition_frontier("s", [_NONEMPTY_FEED]).state)
    seen.add(classify_uk_acquisition_frontier("s", [_EMPTY_FEED]).state)
    seen.add(classify_uk_acquisition_frontier("s", []).state)
    seen.add(classify_uk_acquisition_frontier("s", [_HTTP_404_FEED]).state)
    seen.add(classify_uk_acquisition_frontier("s", [_MULTIPLE_CHOICES_FEED]).state)
    seen.add(classify_uk_acquisition_frontier("s", [_UNPARSEABLE_FEED]).state)
    seen.add(
        classify_uk_acquisition_frontier(
            "s", [], base_source_status="metadata_only"
        ).state
    )
    assert seen == set(UKEffectFeedState)


def test_no_feed_pages_is_page_absent_frontier() -> None:
    state = classify_uk_acquisition_frontier("ukpga/9999/1", [])
    assert state.state is UKEffectFeedState.FEED_PAGE_ABSENT
    assert state.is_acquisition_frontier is True
    assert state.feed_page_count == 0
    assert state.total_entry_count == 0


def test_present_page_wins_over_empty_sibling_page() -> None:
    # A non-empty page anywhere in the set defeats an empty sibling, regardless
    # of order — no Python-order accident decides the frontier.
    forward = classify_uk_acquisition_frontier("s", [_EMPTY_FEED, _NONEMPTY_FEED])
    reverse = classify_uk_acquisition_frontier("s", [_NONEMPTY_FEED, _EMPTY_FEED])
    assert forward.state is UKEffectFeedState.FEED_PRESENT_NONEMPTY
    assert reverse.state is UKEffectFeedState.FEED_PRESENT_NONEMPTY
    assert forward.total_entry_count == reverse.total_entry_count == 1


def test_defect_precedence_is_order_independent() -> None:
    pages = [_EMPTY_FEED, _MULTIPLE_CHOICES_FEED, _HTTP_404_FEED]
    forward = classify_uk_acquisition_frontier("s", pages)
    reverse = classify_uk_acquisition_frontier("s", list(reversed(pages)))
    # multiple_choices outranks http_404 outranks empty.
    assert forward.state is UKEffectFeedState.FEED_MULTIPLE_CHOICES
    assert reverse.state is UKEffectFeedState.FEED_MULTIPLE_CHOICES


def test_base_metadata_only_promotion_only_for_empty_or_absent() -> None:
    promoted = classify_uk_acquisition_frontier(
        "s", [_EMPTY_FEED], base_source_status="metadata_only"
    )
    assert promoted.state is UKEffectFeedState.BASE_METADATA_ONLY
    # The original feed reason is preserved alongside the promoted class.
    assert UKEffectFeedState.FEED_EMPTY in promoted.reasons
    assert UKEffectFeedState.BASE_METADATA_ONLY in promoted.reasons

    absent_promoted = classify_uk_acquisition_frontier(
        "s", [], base_source_status="metadata_only"
    )
    assert absent_promoted.state is UKEffectFeedState.BASE_METADATA_ONLY


def test_base_metadata_only_does_not_override_feed_defect() -> None:
    # A feed that responded with a real defect (Multiple Choices) describes the
    # feed endpoint and must NOT be relabelled as a base-metadata frontier.
    state = classify_uk_acquisition_frontier(
        "s", [_MULTIPLE_CHOICES_FEED], base_source_status="metadata_only"
    )
    assert state.state is UKEffectFeedState.FEED_MULTIPLE_CHOICES


def test_metadata_only_base_does_not_promote_present_feed() -> None:
    state = classify_uk_acquisition_frontier(
        "s", [_NONEMPTY_FEED], base_source_status="metadata_only"
    )
    assert state.state is UKEffectFeedState.FEED_PRESENT_NONEMPTY
    assert state.is_acquisition_frontier is False


def test_diagnostic_detail_is_nonblocking_record() -> None:
    state = classify_uk_acquisition_frontier("ukpga/1945/9", [_EMPTY_FEED])
    detail = state.to_diagnostic_detail()
    assert detail["rule_id"] == "uk_effect_feed_acquisition_frontier_classified"
    assert detail["family"] == "source_pathology"
    assert detail["phase"] == "acquisition"
    assert detail["blocking"] is False
    assert detail["strict_disposition"] == "record"
    assert detail["acquisition_frontier_state"] == "feed_empty"
    assert detail["is_acquisition_frontier"] is True
    assert detail["statute_id"] == "ukpga/1945/9"


def test_report_builder_is_deterministic_and_sorted() -> None:
    states = [
        classify_uk_acquisition_frontier("ukpga/2/2", [_EMPTY_FEED]),
        classify_uk_acquisition_frontier("ukpga/1/1", [_NONEMPTY_FEED]),
        classify_uk_acquisition_frontier("ukpga/3/3", []),
    ]
    report_a = uk_acquisition_frontier_states_to_report(states)
    report_b = uk_acquisition_frontier_states_to_report(list(reversed(states)))
    assert report_a == report_b
    assert [row["statute_id"] for row in report_a["statutes"]] == [
        "ukpga/1/1",
        "ukpga/2/2",
        "ukpga/3/3",
    ]
    assert report_a["acquisition_frontier_statutes"] == ["ukpga/2/2", "ukpga/3/3"]
    assert report_a["acquisition_frontier_statute_count"] == 2
    assert list(report_a["state_counts"].keys()) == sorted(
        report_a["state_counts"].keys()
    )


def test_to_dict_round_trips_state_fields() -> None:
    state = classify_uk_acquisition_frontier(
        "ukpga/4/4", [_EMPTY_FEED], base_source_status="available"
    )
    row = state.to_dict()
    assert row["statute_id"] == "ukpga/4/4"
    assert row["state"] == "feed_empty"
    assert row["is_acquisition_frontier"] is True
    assert row["base_source_status"] == "available"
    assert row["page_states"] == [{"state": "feed_empty", "size": len(_EMPTY_FEED), "entry_count": 0}]


def test_frozen_carriers_are_immutable() -> None:
    page = UKEffectFeedPageState(state=UKEffectFeedState.FEED_EMPTY, size=10)
    state = UKAcquisitionFrontierState(
        statute_id="s",
        state=UKEffectFeedState.FEED_EMPTY,
        reasons=(UKEffectFeedState.FEED_EMPTY,),
        feed_page_count=1,
        total_entry_count=0,
        page_states=(page,),
    )
    # frozen=True dataclasses raise FrozenInstanceError on attribute assignment.
    assert page.__dataclass_params__.frozen is True
    assert state.__dataclass_params__.frozen is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        page.__setattr__("size", 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.__setattr__("statute_id", "x")
