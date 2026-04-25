"""Tests for the FINLEX_INLINE_REPEAL_STUB → ProvisionTimeline cross-check.

Gap 4 of notes/FINLAND_PROFILE_ONTOLOGY_GAPS_2026-04-15.md §1.9.

Four unit cases:

1. Match: stub claims 2021/1030, timeline terminates slot with 2021/1030
   → editorial_witness_confirmed
2. Mismatch: stub claims 2020/999, timeline terminates slot with 2021/1030
   → editorial_witness_disagrees
3. Unresolved: stub claims 2021/1030, slot has no repeal terminator in timeline
   → editorial_witness_unresolved
4. Clean: no stubs → no evidence records

Plus one integration case for 2013/331 that requires the data archive.

Finland timeline note
---------------------
The Finland timeline uses ``paragraph`` (not ``item``) as the kind for kohta/item
level slots, mirroring ``IRNodeKind.PARAGRAPH`` in the base IR.  The eId parsing
helper ``_slot_addresses_for_stub`` produces addresses with ``("paragraph", N)``
leaf pairs.  Unit tests use the same kind so that direct-slot lookup (Strategy 1)
works; the integration test exercises the ancestor drill-down path (Strategy 2)
which is how real Finland replay timelines are structured.
"""
from __future__ import annotations

import pytest
from lxml import etree

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind as NK
from lawvm.finland.editorial_adjudication import (
    _eid_parent_path,
    _slot_addresses_for_stub,
    _item_addresses_for_stub,  # deprecated alias, kept for backward compat
    cross_check_stub_observations,
    collect_and_cross_check,
    _find_slot_in_content,
)


# ---------------------------------------------------------------------------
# Helpers — use "paragraph" kind to match Finland timeline structure
# ---------------------------------------------------------------------------

def _repeal_placeholder_node() -> IRNode:
    """Minimal repeal placeholder IRNode (paragraph kind with placeholder marker)."""
    return IRNode(kind=NK.PARAGRAPH, attrs={"lawvm_repeal_placeholder": "1"})


def _make_repeal_version(statute_id: str, effective: str = "2021-12-01") -> ProvisionVersion:
    """Build a ProvisionVersion representing a repeal by *statute_id*."""
    return ProvisionVersion(
        effective=effective,
        enacted=effective,
        content=_repeal_placeholder_node(),
        source=OperationSource(statute_id=statute_id),
    )


def _make_live_version(effective: str = "2014-01-01") -> ProvisionVersion:
    """Build a ProvisionVersion representing the initial live text (not a repeal)."""
    return ProvisionVersion(
        effective=effective,
        enacted=effective,
        content=IRNode(kind=NK.PARAGRAPH, text="tavanomaisella jätteellä ..."),
    )


def _addr_sec3_sub1_para2() -> LegalAddress:
    """Address matching what _slot_addresses_for_stub produces for the 2013/331 stub."""
    return LegalAddress(
        path=(("chapter", "1"), ("section", "3"), ("subsection", "1"), ("paragraph", "2"))
    )


def _timeline_with_direct_repeal(statute_id: str) -> ProvisionTimeline:
    """Timeline for sec3/sub1/paragraph:2 with a direct repeal version."""
    addr = _addr_sec3_sub1_para2()
    return ProvisionTimeline(
        address=addr,
        versions=[
            _make_live_version(),
            _make_repeal_version(statute_id),
        ],
    )


def _timeline_no_repeal() -> ProvisionTimeline:
    """Timeline for sec3/sub1/paragraph:2 with only a live version (no repeal)."""
    addr = _addr_sec3_sub1_para2()
    return ProvisionTimeline(
        address=addr,
        versions=[_make_live_version()],
    )


_STUB_OBS_MATCH: dict = {
    "kind": "FINLEX_INLINE_REPEAL_STUB",
    "eId": "chp_1__sec_3__subsec_1__para_2v20211030",
    "target_range": [2],
    "amendment_id": "2021/1030",
}


# ---------------------------------------------------------------------------
# eId parsing unit tests
# ---------------------------------------------------------------------------

class TestEidParentPath:
    def test_standard_stub_eid(self) -> None:
        """chp_1__sec_3__subsec_1__para_2v20211030 → chapter/section/subsection path."""
        path = _eid_parent_path("chp_1__sec_3__subsec_1__para_2v20211030")
        assert path == (
            ("chapter", "1"),
            ("section", "3"),
            ("subsection", "1"),
        )

    def test_no_chapter(self) -> None:
        """sec_5__subsec_2__para_1v20210101 → section/subsection (no chapter)."""
        path = _eid_parent_path("sec_5__subsec_2__para_1v20210101")
        assert path == (
            ("section", "5"),
            ("subsection", "2"),
        )

    def test_empty_eid(self) -> None:
        assert _eid_parent_path("") == ()

    def test_single_component(self) -> None:
        """Only one component — parent path is empty (nothing above the para)."""
        assert _eid_parent_path("para_1v20211030") == ()


class TestSlotAddressesForStub:
    def test_single_target_produces_paragraph_kind(self) -> None:
        """Address leaf kind must be 'paragraph' to match Finland timeline structure."""
        obs = {"eId": "chp_1__sec_3__subsec_1__para_2v20211030", "target_range": [2]}
        addrs = _slot_addresses_for_stub(obs)
        assert len(addrs) == 1
        assert addrs[0] == LegalAddress(
            path=(("chapter", "1"), ("section", "3"), ("subsection", "1"), ("paragraph", "2"))
        )
        assert addrs[0].path[-1][0] == "paragraph"

    def test_range_target(self) -> None:
        obs = {"eId": "chp_1__sec_3__subsec_1__para_1v20211030", "target_range": [1, 2]}
        addrs = _slot_addresses_for_stub(obs)
        assert len(addrs) == 2
        assert addrs[0].path[-1] == ("paragraph", "1")
        assert addrs[1].path[-1] == ("paragraph", "2")

    def test_empty_range(self) -> None:
        obs = {"eId": "chp_1__sec_3__subsec_1__para_2v20211030", "target_range": []}
        assert _slot_addresses_for_stub(obs) == []

    def test_missing_eid(self) -> None:
        obs = {"target_range": [2]}
        assert _slot_addresses_for_stub(obs) == []

    def test_item_addresses_alias_identical(self) -> None:
        """_item_addresses_for_stub is an alias for _slot_addresses_for_stub."""
        obs = {"eId": "chp_1__sec_3__subsec_1__para_2v20211030", "target_range": [2]}
        assert _item_addresses_for_stub(obs) == _slot_addresses_for_stub(obs)


# ---------------------------------------------------------------------------
# _find_slot_in_content helper tests
# ---------------------------------------------------------------------------

class TestFindSlotInContent:
    def test_finds_direct_child(self) -> None:
        """A paragraph labeled '2' as a direct child is found."""
        placeholder = IRNode(kind=NK.PARAGRAPH, label="2", attrs={"lawvm_repeal_placeholder": "1"})
        section = IRNode(kind=NK.SECTION, children=(
            IRNode(kind=NK.SUBSECTION, children=(
                IRNode(kind=NK.PARAGRAPH, label="1"),
                placeholder,
            )),
        ))
        found = _find_slot_in_content(section, "2")
        assert found is placeholder

    def test_returns_none_when_not_found(self) -> None:
        section = IRNode(kind=NK.SECTION, children=(
            IRNode(kind=NK.PARAGRAPH, label="1"),
        ))
        assert _find_slot_in_content(section, "99") is None


# ---------------------------------------------------------------------------
# Case 1: Match → editorial_witness_confirmed  (direct slot strategy)
# ---------------------------------------------------------------------------

class TestMatchCase:
    def test_confirmed_emitted(self) -> None:
        """Stub claims 2021/1030; direct timeline entry terminates with 2021/1030."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], timelines)
        assert len(evidence) == 1
        rec = evidence[0]
        assert rec["kind"] == "editorial_witness_confirmed"
        assert rec["amendment_id"] == "2021/1030"
        assert "section:3" in rec["slot_address"]
        assert "paragraph:2" in rec["slot_address"]

    def test_confirmed_slot_address_format(self) -> None:
        """slot_address should be the LegalAddress str representation."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], timelines)
        assert evidence[0]["slot_address"] == str(addr)

    def test_no_timeline_terminator_key_in_confirmed(self) -> None:
        """Confirmed records do not carry timeline_terminator key."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], timelines)
        assert "timeline_terminator" not in evidence[0]


# ---------------------------------------------------------------------------
# Ancestor drill-down: match via section content tree (Strategy 2)
# ---------------------------------------------------------------------------

class TestAncestorDrillDown:
    """Verify that repeal embedded in an ancestor section's content is found."""

    def _make_section_content_with_repeal(self, repeal_label: str) -> IRNode:
        """Build a section IRNode where paragraph labeled *repeal_label* is a placeholder."""
        placeholder = IRNode(kind=NK.PARAGRAPH, label=repeal_label,
                             attrs={"lawvm_repeal_placeholder": "1"})
        live = IRNode(kind=NK.PARAGRAPH, label="1", text="kaatopaikalla ...")
        subsec = IRNode(kind=NK.SUBSECTION, label="1", children=(live, placeholder))
        return IRNode(kind=NK.SECTION, label="3", children=(subsec,))

    def test_confirmed_via_section_content(self) -> None:
        """2021/1030 version of section:3 contains placeholder for paragraph:2."""
        sec3_addr = LegalAddress(path=(("chapter", "1"), ("section", "3")))
        sec_content = self._make_section_content_with_repeal("2")
        sec_tl = ProvisionTimeline(address=sec3_addr, versions=[
            ProvisionVersion(
                effective="0000-00-00",
                content=IRNode(kind=NK.SECTION, label="3", text="original"),
            ),
            ProvisionVersion(
                effective="2021-12-01",
                content=sec_content,
                source=OperationSource(statute_id="2021/1030"),
            ),
        ])
        timelines = {sec3_addr: sec_tl}
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], timelines)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_confirmed"
        assert evidence[0]["amendment_id"] == "2021/1030"

    def test_disagrees_via_section_content(self) -> None:
        """Stub claims 2020/999; section content repeal was by 2021/1030 → disagrees."""
        sec3_addr = LegalAddress(path=(("chapter", "1"), ("section", "3")))
        sec_content = self._make_section_content_with_repeal("2")
        sec_tl = ProvisionTimeline(address=sec3_addr, versions=[
            ProvisionVersion(
                effective="2021-12-01",
                content=sec_content,
                source=OperationSource(statute_id="2021/1030"),
            ),
        ])
        timelines = {sec3_addr: sec_tl}
        obs = {**_STUB_OBS_MATCH, "amendment_id": "2020/999"}
        evidence = cross_check_stub_observations([obs], timelines)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_disagrees"
        assert evidence[0]["timeline_terminator"] == "2021/1030"


# ---------------------------------------------------------------------------
# Case 2: Mismatch → editorial_witness_disagrees
# ---------------------------------------------------------------------------

class TestMismatchCase:
    def test_disagrees_emitted(self) -> None:
        """Stub claims 2020/999; timeline entry terminates with 2021/1030 → disagrees."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        obs = {**_STUB_OBS_MATCH, "amendment_id": "2020/999"}
        evidence = cross_check_stub_observations([obs], timelines)
        assert len(evidence) == 1
        rec = evidence[0]
        assert rec["kind"] == "editorial_witness_disagrees"
        assert rec["severity"] == "REQUIRES_TRIAGE"
        assert rec["amendment_id"] == "2020/999"
        assert rec["timeline_terminator"] == "2021/1030"
        assert "paragraph:2" in rec["slot_address"]


# ---------------------------------------------------------------------------
# Case 3: Unresolved → editorial_witness_unresolved
# ---------------------------------------------------------------------------

class TestUnresolvedCase:
    def test_unresolved_no_timeline_entry(self) -> None:
        """Timeline has no entry for the slot → unresolved."""
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], {})
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_unresolved"
        assert evidence[0]["amendment_id"] == "2021/1030"
        assert evidence[0]["timeline_terminator"] is None

    def test_unresolved_no_repeal_in_timeline(self) -> None:
        """Timeline exists but has no repeal version → unresolved."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_no_repeal()}
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], timelines)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_unresolved"
        assert evidence[0]["timeline_terminator"] is None

    def test_unresolved_none_timelines(self) -> None:
        """When timelines=None, all stubs become unresolved."""
        evidence = cross_check_stub_observations([_STUB_OBS_MATCH], None)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_unresolved"


# ---------------------------------------------------------------------------
# Case 4: No stubs → no evidence
# ---------------------------------------------------------------------------

class TestCleanCase:
    def test_empty_observations(self) -> None:
        """Zero stubs → zero evidence records."""
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = cross_check_stub_observations([], timelines)
        assert evidence == []

    def test_non_stub_observations_ignored(self) -> None:
        """Observations with other kinds are silently skipped."""
        obs = {"kind": "ORACLE_DUPLICATE_CHILD_LABEL", "detail": "2"}
        evidence = cross_check_stub_observations([obs], {})
        assert evidence == []


# ---------------------------------------------------------------------------
# Range stub: two items in one observation
# ---------------------------------------------------------------------------

class TestRangeStub:
    def test_range_produces_two_records(self) -> None:
        """A stub covering paragraphs 1–2 should emit two evidence records."""
        addr1 = LegalAddress(path=(("section", "5"), ("subsection", "1"), ("paragraph", "1")))
        addr2 = LegalAddress(path=(("section", "5"), ("subsection", "1"), ("paragraph", "2")))
        tl1 = ProvisionTimeline(address=addr1, versions=[_make_repeal_version("2020/500")])
        tl2 = ProvisionTimeline(address=addr2, versions=[_make_repeal_version("2020/500")])
        timelines = {addr1: tl1, addr2: tl2}
        obs = {
            "kind": "FINLEX_INLINE_REPEAL_STUB",
            "eId": "sec_5__subsec_1__para_1v20200101",
            "target_range": [1, 2],
            "amendment_id": "2020/500",
        }
        evidence = cross_check_stub_observations([obs], timelines)
        assert len(evidence) == 2
        assert all(r["kind"] == "editorial_witness_confirmed" for r in evidence)

    def test_range_partial_mismatch(self) -> None:
        """paragraph 1 matches, paragraph 2 mismatches → one confirmed, one disagrees."""
        addr1 = LegalAddress(path=(("section", "5"), ("subsection", "1"), ("paragraph", "1")))
        addr2 = LegalAddress(path=(("section", "5"), ("subsection", "1"), ("paragraph", "2")))
        tl1 = ProvisionTimeline(address=addr1, versions=[_make_repeal_version("2020/500")])
        tl2 = ProvisionTimeline(address=addr2, versions=[_make_repeal_version("2021/999")])
        timelines = {addr1: tl1, addr2: tl2}
        obs = {
            "kind": "FINLEX_INLINE_REPEAL_STUB",
            "eId": "sec_5__subsec_1__para_1v20200101",
            "target_range": [1, 2],
            "amendment_id": "2020/500",
        }
        evidence = cross_check_stub_observations([obs], timelines)
        kinds = {r["kind"] for r in evidence}
        assert "editorial_witness_confirmed" in kinds
        assert "editorial_witness_disagrees" in kinds


# ---------------------------------------------------------------------------
# collect_and_cross_check: XML tree walker
# ---------------------------------------------------------------------------

def _make_subsection_with_stub_xml() -> etree._Element:
    """Build a <subsection> XML element containing a Finlex inline repeal stub."""
    return etree.fromstring("""
    <subsection eId="chp_1__sec_3__subsec_1">
      <paragraph eId="chp_1__sec_3__subsec_1__para_1">
        <num>1)</num>
        <content><p>kaatopaikalla jätteiden loppukäsittelypaikkaa</p></content>
      </paragraph>
      <paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">
        <content><p><i>2 kohta on kumottu A:lla 25.11.2021/1030.</i></p></content>
      </paragraph>
      <paragraph eId="chp_1__sec_3__subsec_1__para_3">
        <num>3)</num>
        <content><p>pysyvällä jätteellä jätettä joka ei hajoa</p></content>
      </paragraph>
    </subsection>
    """)


class TestCollectAndCrossCheck:
    def test_collects_from_direct_children(self) -> None:
        """collect_and_cross_check finds stub as direct child of subsection."""
        subsec = _make_subsection_with_stub_xml()
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = collect_and_cross_check(subsec, timelines)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_confirmed"

    def test_collects_from_nested_subsection(self) -> None:
        """collect_and_cross_check finds stub nested inside a section element."""
        section = etree.fromstring("""
        <section eId="chp_1__sec_3">
          <num>3 §</num>
          <subsection eId="chp_1__sec_3__subsec_1">
            <paragraph eId="chp_1__sec_3__subsec_1__para_2v20211030">
              <content><p><i>2 kohta on kumottu A:lla 25.11.2021/1030.</i></p></content>
            </paragraph>
          </subsection>
        </section>
        """)
        addr = _addr_sec3_sub1_para2()
        timelines = {addr: _timeline_with_direct_repeal("2021/1030")}
        evidence = collect_and_cross_check(section, timelines)
        assert len(evidence) == 1
        assert evidence[0]["kind"] == "editorial_witness_confirmed"

    def test_non_element_returns_empty(self) -> None:
        """Non-XML input returns empty list without raising."""
        evidence = collect_and_cross_check("not an element", {})
        assert evidence == []

    def test_no_stubs_returns_empty(self) -> None:
        """Clean XML with no stubs produces no evidence."""
        clean = etree.fromstring("""
        <subsection eId="sec_1__subsec_1">
          <paragraph eId="sec_1__subsec_1__para_1">
            <num>1)</num>
            <content><p>ensimmäinen kohta</p></content>
          </paragraph>
        </subsection>
        """)
        evidence = collect_and_cross_check(clean, {})
        assert evidence == []


# ---------------------------------------------------------------------------
# Integration: 2013/331 → editorial_witness_confirmed for 2021/1030, paragraph 2
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not __import__("pathlib").Path("data/finlex.farchive").exists(),
    reason="Requires data/finlex.farchive",
)
class TestIntegration2013331:
    """Live integration test against the corpus archive.

    Verifies that:
    1. pinned_replay("2013/331") builds timelines that record 2021/1030 as the
       amendment that deposited a repeal placeholder in the section:3 content
       for paragraph:2.
    2. collect_and_cross_check on the @20211030 oracle XML for sec 3 emits
       one editorial_witness_confirmed record for 2021/1030.

    Lookup path:
    - stub eId → chp_1__sec_3__subsec_1__para_2v20211030
    - parsed slot address → chapter:1/section:3/subsection:1/paragraph:2
    - Strategy 2 (ancestor drill-down): section:3 timeline has 2021/1030 version
      whose content tree contains paragraph:2 with lawvm_repeal_placeholder
    """

    def _get_replay_and_oracle(self):
        from lawvm.corpus_store import get_corpus_store
        from lawvm.finland.corpus import get_ground_truth_tree
        from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
        from lawvm.tools.section_keys import extract_oracle_sections
        from tests.corpus_pin_helpers import pinned_replay

        cs = get_corpus_store()
        rr = pinned_replay("2013/331", quiet=True, corpus=cs)
        selector = ConsolidatedArtifactSelector.exact_embedded_version("20211030")
        root = get_ground_truth_tree("2013/331", corpus=cs, selector=selector)
        assert root is not None
        sections = extract_oracle_sections(root)
        return rr, sections

    def test_editorial_witness_confirmed_for_2013_331(self) -> None:
        """Confirmed record fires: stub 2021/1030 matches timeline's section content."""
        rr, sections = self._get_replay_and_oracle()
        timelines = rr.timelines
        assert timelines is not None, "ReplayResult.timelines must be populated"

        sec3 = sections.get("chapter:1/section:3")
        assert sec3 is not None, "section:3 not found in oracle"

        evidence = collect_and_cross_check(sec3, timelines)
        confirmed = [r for r in evidence if r["kind"] == "editorial_witness_confirmed"]
        assert len(confirmed) >= 1, (
            f"Expected at least one editorial_witness_confirmed, got: {evidence!r}"
        )
        # The confirmed record should be for the 2021/1030 repeal of paragraph:2.
        match_record = next(
            (r for r in confirmed if r["amendment_id"] == "2021/1030"),
            None,
        )
        assert match_record is not None, (
            f"No confirmed record for 2021/1030 in {confirmed!r}"
        )
        assert "paragraph:2" in match_record["slot_address"], (
            f"slot_address does not contain 'paragraph:2': {match_record['slot_address']!r}"
        )

    def test_no_disagrees_for_2013_331(self) -> None:
        """No editorial_witness_disagrees should fire on the clean post-repeal oracle."""
        rr, sections = self._get_replay_and_oracle()
        timelines = rr.timelines
        assert timelines is not None

        sec3 = sections.get("chapter:1/section:3")
        assert sec3 is not None

        evidence = collect_and_cross_check(sec3, timelines)
        disagrees = [r for r in evidence if r["kind"] == "editorial_witness_disagrees"]
        assert disagrees == [], (
            f"Unexpected editorial_witness_disagrees records: {disagrees!r}"
        )
