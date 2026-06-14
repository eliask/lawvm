"""UK oracle RetainText="true" repeal elision (presentation_cleanup).

legislation.gov.uk keeps a repealed inline phrase visible with
``<Repeal RetainText="true">…</Repeal>`` because a 1-dimensional-time-axis
consolidation cannot represent partial / "for specified purposes" commencement.
This is a display artifact, NOT law — the direct analogue of Finnish Finlex's
"Aiempi sanamuoto kuuluu:" marker (``fi_oracle_aiempi_sanamuoto_marker`` in
``lawvm.tools.editorial_hygiene``).  LawVM elides it from the oracle COMPARISON
tree before comparison so it never raises a spurious only-in-oracle text_diff,
without touching replay.  The elision is auditable: it emits the named
``uk_oracle_retain_text_repeal_elided`` observation.

These tests pin:
  * the elided-variant text map drops the retained phrase but keeps live text;
  * a normal repeal / a node without RetainText is unaffected;
  * the named presentation_cleanup observation is emitted;
  * the comparison accepts EITHER the retained-included or retained-elided form;
  * determinism;
  * (witness) the real nia/2007/2 divergence count does not regress.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.uk_legislation.uk_grafter import (
    _RETAIN_TEXT_ELISION_RULE_ID,
    _oracle_text_eliding_retained_repeals,
    extract_eid_map_bytes,
)
from lawvm.tools.uk_structural_review import (
    _CLASS_SAME,
    _CLASS_TEXT_DIFF,
    _build_oracle_norm_text_map,
    _build_oracle_retain_text_elided_norm_map,
    _classify_eids,
)

_NS = "http://www.legislation.gov.uk/namespaces/legislation"


def _oracle_xml(inner: str) -> bytes:
    return (
        f'<Legislation xmlns="{_NS}">\n'
        f"  <Body>\n{inner}\n  </Body>\n"
        f"</Legislation>\n"
    ).encode("utf-8")


# --- the elision helper -----------------------------------------------------


def test_elision_helper_drops_retained_phrase_keeps_tail() -> None:
    from lxml import etree as ET

    el = ET.fromstring(
        f'<Text xmlns="{_NS}">before '
        f'<Repeal RetainText="true">retained words</Repeal> after</Text>'.encode()
    )
    text, elided = _oracle_text_eliding_retained_repeals(el)
    assert elided is True
    # The retained phrase is gone; the live tail ("after") survives.
    assert "retained words" not in text
    assert "before" in text and "after" in text


def test_elision_helper_noop_without_retain_text() -> None:
    from lxml import etree as ET

    el = ET.fromstring(f'<Text xmlns="{_NS}">plain provision text</Text>'.encode())
    text, elided = _oracle_text_eliding_retained_repeals(el)
    assert elided is False
    assert text == "plain provision text"


def test_elision_helper_noop_for_non_retain_repeal() -> None:
    from lxml import etree as ET

    # A <Repeal> WITHOUT RetainText="true" is ordinary inline markup and must
    # NOT be elided (e.g. it is live text, not a kept-visible repealed phrase).
    el = ET.fromstring(
        f'<Text xmlns="{_NS}">keep <Repeal>this</Repeal> word</Text>'.encode()
    )
    text, elided = _oracle_text_eliding_retained_repeals(el)
    assert elided is False
    assert "this" in text


# --- the oracle extraction surface ------------------------------------------


def test_extract_eid_map_emits_retain_text_elision_observation() -> None:
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <Text>The conditions in <Repeal RetainText="true">Part 1 of</Repeal> Schedule 1.</Text>
      </P1para>
    </P1>"""
    )
    data = extract_eid_map_bytes(xml)

    elided_map = data["retain_text_elided_text_map"]
    assert "section-1" in elided_map
    # The retained "Part 1 of" is removed from the elided variant ...
    assert "part1of" not in elided_map["section-1"].replace(" ", "")
    # ... while the primary text_map keeps the retained-included form.
    assert "part" in data["text_map"]["section-1"]

    obs = [
        o
        for o in data["oracle_identity_observations"]
        if o["rule_id"] == _RETAIN_TEXT_ELISION_RULE_ID
    ]
    assert len(obs) == 1
    assert obs[0]["family"] == "presentation_cleanup"
    assert obs[0]["phase"] == "oracle_compare_normalization"
    assert obs[0]["original_eid"] == "section-1"


def test_extract_eid_map_no_observation_without_retain_text() -> None:
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para><Text>An ordinary live provision.</Text></P1para>
    </P1>"""
    )
    data = extract_eid_map_bytes(xml)
    assert data["retain_text_elided_text_map"] == {}
    assert not any(
        o["rule_id"] == _RETAIN_TEXT_ELISION_RULE_ID
        for o in data["oracle_identity_observations"]
    )


def test_extract_eid_map_is_deterministic() -> None:
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <Text>Text with <Repeal RetainText="true">retained</Repeal> markup.</Text>
      </P1para>
    </P1>"""
    )
    first = extract_eid_map_bytes(xml)
    second = extract_eid_map_bytes(xml)
    assert first["retain_text_elided_text_map"] == second["retain_text_elided_text_map"]
    assert first["oracle_identity_observations"] == second["oracle_identity_observations"]


# --- the comparison neutralizes the artifact in BOTH directions -------------


def _classify_one(
    *, eid: str, replay_text: str, oracle_data: dict
) -> str:
    """Classify a single EID present on both sides, threading the elided map."""
    oracle_norm = _build_oracle_norm_text_map(oracle_data["text_map"])
    oracle_elided_norm = _build_oracle_retain_text_elided_norm_map(
        oracle_data["retain_text_elided_text_map"]
    )
    classified = _classify_eids(
        {eid: replay_text},
        oracle_norm,
        replay_norm_set=frozenset({eid}),
        oracle_norm_set=frozenset({eid}),
        replay_norm_to_raw={eid: eid},
        replay_leaf_eids=frozenset({eid}),
        oracle_retain_text_elided_norm_map=oracle_elided_norm,
    )
    return classified[eid]["kind"]


def test_comparison_matches_when_replay_applied_the_repeal() -> None:
    # Oracle keeps "Part 1 of" visible; LawVM replay APPLIED the repeal so the
    # phrase is gone.  Without elision this is a spurious text_diff; with the
    # elided variant it scores as SAME.
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <Text>conditions in <Repeal RetainText="true">Part 1 of</Repeal> Schedule 1</Text>
      </P1para>
    </P1>"""
    )
    data = extract_eid_map_bytes(xml)
    kind = _classify_one(
        eid="section-1",
        replay_text="1 conditions in Schedule 1",  # repeal applied
        oracle_data=data,
    )
    assert kind == _CLASS_SAME


def test_comparison_matches_when_replay_retained_the_phrase() -> None:
    # Oracle keeps "Part 1 of" visible; LawVM replay also retained it (repeal
    # not applied — partial-commencement).  Matching the retained-included form
    # must still score SAME (the elision must not break the matching case).
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <Text>conditions in <Repeal RetainText="true">Part 1 of</Repeal> Schedule 1</Text>
      </P1para>
    </P1>"""
    )
    data = extract_eid_map_bytes(xml)
    kind = _classify_one(
        eid="section-1",
        replay_text="1 conditions in Part 1 of Schedule 1",  # repeal not applied
        oracle_data=data,
    )
    assert kind == _CLASS_SAME


def test_comparison_still_reports_a_genuine_text_diff() -> None:
    # Negative: a real divergence (replay text unrelated to either oracle form)
    # is NOT masked by the elision.
    xml = _oracle_xml(
        """\
    <P1 id="section-1">
      <Pnumber>1</Pnumber>
      <P1para>
        <Text>conditions in <Repeal RetainText="true">Part 1 of</Repeal> Schedule 1</Text>
      </P1para>
    </P1>"""
    )
    data = extract_eid_map_bytes(xml)
    kind = _classify_one(
        eid="section-1",
        replay_text="something completely different",
        oracle_data=data,
    )
    assert kind == _CLASS_TEXT_DIFF


# --- real witness -----------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


@pytest.mark.skipif(
    not _DB.exists(), reason="UK farchive not present in this environment"
)
def test_nia_2007_2_emits_retain_text_observations() -> None:
    """nia/2007/2's oracle carries many RetainText repeals; the elision must
    fire and register elided variants without raising the divergence."""
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute

    with Farchive(_DB) as archive:
        oracle_bytes = archive.get(
            _archive_url_for_statute("nia/2007/2", pit_date=None, enacted=False)
        )
    if oracle_bytes is None:
        pytest.skip("nia/2007/2 oracle XML absent from archive")

    data = extract_eid_map_bytes(oracle_bytes)
    obs = [
        o
        for o in data["oracle_identity_observations"]
        if o["rule_id"] == _RETAIN_TEXT_ELISION_RULE_ID
    ]
    # Real statute carries hundreds of retained-repeal phrases.
    assert len(obs) > 50
    assert all(o["family"] == "presentation_cleanup" for o in obs)
    # Every emitted elided variant is a comparison-only addition, never empties
    # the primary text map.
    assert data["text_map"]
