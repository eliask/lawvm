from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from lxml import etree
import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import MigrationEvent, OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.source_witness import source_witness_digest_coverage
from lawvm.provision_state import resolve_provision_state
from lawvm.tools.provision_state import (
    _hash_payload,
    _lawvm_code_identity,
    build_provision_state_response,
    main,
    resolve_address,
    resolve_address_for_query,
)


def _section(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="1", text=text)


def _numbered_section(label: str, children: tuple[IRNode, ...]) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=children)


def _subsection(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text)


def _timeline(*, expires: str = "") -> dict[LegalAddress, ProvisionTimeline]:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    content = _section("A provision duty.")
    return _timeline_with_content(content, expires=expires)


def _timeline_with_content(
    content: IRNode,
    *,
    expires: str = "",
    raw_text: str = "Section 1 is replaced with a new duty.",
) -> dict[LegalAddress, ProvisionTimeline]:
    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    version = ProvisionVersion(
        effective="2020-01-01",
        enacted="2019-12-01",
        expires=expires,
        content=content,
        source=OperationSource(
            statute_id="2019/1",
            title="Amending Act",
            enacted="2019-12-01",
            effective="2020-01-01",
            raw_text=raw_text,
        ),
        content_hash=irnode_content_hash(content),
    )
    return {address: ProvisionTimeline(address=address, versions=[version])}


def test_provision_state_response_exposes_text_hash_and_temporal_pin() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )

    assert payload["schema"] == "lawvm.provision_state.v1"
    assert payload["spec_version"] == "0.3"
    assert payload["provision_status"] == "selected"
    assert payload["resolved_address"]["text"] == "chapter:1/section:1"
    assert payload["address_match"]["mode"] == "unique_suffix"
    assert payload["text"]["rendered"] == "A provision duty."
    assert payload["hashes"]["content_hash"] == irnode_content_hash(_section("A provision duty."))
    assert len(payload["hashes"]["structured_content_hash"]) == 64
    assert "IRNode.to_jsonable_dict" in payload["hashes"]["structured_content_hash_semantics"]
    assert len(payload["hashes"]["derived_state_hash"]) == 64
    assert "lineage control fields" in payload["hashes"]["derived_state_hash_semantics"]
    assert payload["version"]["effective"] == "2020-01-01"
    assert payload["version"]["enacted"] == "2019-12-01"
    assert payload["source"]["statute_id"] == "2019/1"
    assert payload["source_locator_status"] == "canonical_document_locator"
    assert payload["source_locator"]["artifact_kind"] == "operation_source_statute_xml"
    assert payload["source_locator"]["document_uri"] == "finlex://sd/2019/1/fin/main.xml"
    assert payload["source_locator"]["structural_path"] == "lawvm-target:chapter:1/section:1"
    assert payload["source_locator"]["detail"]["document_locator_status"] == "canonical_finlex_document_uri"
    assert payload["source_locator"]["detail"]["precision"] == "document_plus_resolved_target_legal_address"
    assert payload["source_locator"]["detail"]["target_legal_address_kind"] == "lawvm_resolved_target"
    assert payload["source_locator"]["detail"]["target_address_authority"] == (
        "resolved_replay_timeline_address"
    )
    assert payload["source_locator"]["detail"]["target_xpath_candidate"].startswith(
        "//*[local-name()='body']"
    )
    xml = etree.fromstring(
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><chapter><num>1 luku</num><section><num>1 \xc2\xa7</num></section></chapter></body></act>
        </akomaNtoso>
        """
    )
    matches = xml.xpath(payload["source_locator"]["detail"]["target_xpath_candidate"])
    assert isinstance(matches, list)
    assert len(matches) == 1
    assert payload["source_locator"]["detail"]["target_xpath_candidate_status"] == (
        "finlex_structural_xpath_candidate"
    )
    assert payload["source_locator"]["detail"]["xpath_status"] == (
        "unavailable_operation_source_target_not_xml_anchored"
    )
    assert payload["source_locator"]["detail"]["byte_span_status"] == "unavailable_initial_surface"
    assert payload["source_locator"]["detail"]["hash_role"] == "excluded_from_derived_state_hash"
    assert payload["source_locator"]["quote_hash"]
    assert payload["source_locator"]["detail"]["source_witness_status"] == (
        "operation_source_raw_text_available"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["kind"] == "operation_source_raw_text"
    assert payload["source_locator"]["detail"]["source_witness"]["source_role"] == (
        "operation_source_raw_text"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["artifact_id"] == "2019/1"
    assert payload["source_locator"]["detail"]["source_witness"]["locator"] == (
        "finlex://sd/2019/1/fin/main.xml"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["source_lane"] == (
        "finlex_source_xml"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["bounded_preview"] == (
        "Section 1 is replaced with a new duty."
    )
    assert payload["source_locator"]["detail"]["source_witness"]["preview_digest_algorithm"] == (
        "sha256"
    )
    assert source_witness_digest_coverage(payload["source_locator"]["detail"]["source_witness"]) == (
        "preview_digest"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["quote"] == (
        "Section 1 is replaced with a new duty."
    )
    assert payload["source_locator"]["detail"]["source_witness"]["quote_truncated"] is False
    assert payload["source_locator"]["detail"]["source_witness"]["quote_char_span"] == [0, 38]
    assert payload["source_locator"]["detail"]["source_witness"]["full_raw_text_char_span"] == [0, 38]
    assert payload["source_locator"]["detail"]["source_witness"]["char_span_status"] == (
        "operation_source_raw_text_char_span"
    )
    assert payload["source_locator"]["detail"]["source_witness"]["char_span_basis"] == (
        "OperationSource.raw_text after boundary whitespace trimming"
    )
    assert payload["lineage"]["lineage_status"] == "self_only"
    assert payload["lineage"]["address_chain"] == [payload["resolved_address"]]
    assert payload["engine"]["producer"] == "lawvm"
    assert payload["engine"]["interface"] == "lawvm provision-state"
    assert {"build_id", "git_commit", "git_dirty", "repository"} <= set(payload["engine"])


def test_provision_state_masks_descendant_under_selected_chapter_tombstone() -> None:
    chapter_address = LegalAddress(path=(("chapter", "8a"),))
    section_address = LegalAddress(path=(("chapter", "8a"), ("section", "68a")))
    section = IRNode(kind=IRNodeKind.SECTION, label="68a", text="Live child text")
    timelines = {
        chapter_address: ProvisionTimeline(
            address=chapter_address,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-01",
                    content=IRNode(kind=IRNodeKind.CHAPTER, label="8a"),
                    source=OperationSource(statute_id="2019/1", effective="2020-01-01"),
                ),
                ProvisionVersion(
                    effective="2025-01-01",
                    enacted="2024-12-01",
                    content=None,
                    source=OperationSource(statute_id="2024/1", effective="2025-01-01"),
                ),
            ],
        ),
        section_address: ProvisionTimeline(
            address=section_address,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-01",
                    content=section,
                    source=OperationSource(statute_id="2019/1", effective="2020-01-01"),
                    content_hash=irnode_content_hash(section),
                ),
            ],
        ),
    }

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:8a/section:68a",
        as_of="2026-01-01",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert payload["resolved_address"]["text"] == "chapter:8a/section:68a"
    assert payload["version"]["content_state"] == "tombstone"
    assert payload["source"]["statute_id"] == "2024/1"
    assert payload["text"]["available"] is False


def test_operation_source_locator_anchors_exact_raw_quote_in_source_xml() -> None:
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section><content>Section 1 is replaced with a new duty.</content></section></body></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    quote = "Section 1 is replaced with a new duty."
    expected_start = source_text.index(quote)
    expected_end = expected_start + len(quote)
    expected_char_span = [expected_start, expected_end]
    expected_byte_span = [
        len(source_text[:expected_start].encode("utf-8")),
        len(source_text[:expected_end].encode("utf-8")),
    ]
    expected_digest = hashlib.sha256(source_xml).hexdigest()

    without_span = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    witness = locator["detail"]["source_witness"]
    assert locator["artifact_kind"] == "operation_source_statute_xml"
    assert locator["artifact_digest"] == expected_digest
    assert locator["artifact_digest_algorithm"] == "sha256"
    assert "xpath" not in locator
    assert locator["char_span"] == expected_char_span
    assert locator["byte_span"] == expected_byte_span
    assert locator["detail"]["operation_source_xml_span_status"] == "available"
    assert locator["detail"]["artifact_digest"] == expected_digest
    assert locator["detail"]["artifact_digest_algorithm"] == "sha256"
    assert locator["detail"]["artifact_digest_status"] == "source_xml_bytes_sha256"
    assert locator["detail"]["char_span_status"] == "operation_source_raw_xml_quote_scan"
    assert locator["detail"]["byte_span_status"] == "operation_source_raw_xml_quote_scan_utf8"
    assert witness["artifact_char_span"] == expected_char_span
    assert witness["artifact_byte_span"] == expected_byte_span
    assert witness["artifact_span_status"] == "operation_source_raw_xml_quote_scan"
    assert witness["artifact_span_match_count"] == 1
    assert witness["source_role"] == "operation_source_raw_text"
    assert witness["artifact_id"] == "2019/1"
    assert witness["locator"] == "finlex://sd/2019/1/fin/main.xml"
    assert witness["source_lane"] == "finlex_source_xml"
    assert witness["digest_algorithm"] == "sha256"
    assert witness["digest"] == expected_digest
    assert witness["preview_digest_algorithm"] == "sha256"
    assert witness["bounded_preview"] == quote
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"
    assert payload["hashes"]["content_hash"] == without_span["hashes"]["content_hash"]
    assert payload["hashes"]["derived_state_hash"] == without_span["hashes"]["derived_state_hash"]


def test_operation_source_locator_anchors_markup_split_quote_to_xml_text_container() -> None:
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section eId="sec_1"><p eId="p_1">Section <b>1</b> is replaced with a new duty.</p></section></body></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<p eId="p_1">')
    expected_end = source_text.index("</p>", expected_start) + len("</p>")
    expected_char_span = [expected_start, expected_end]
    expected_byte_span = [
        len(source_text[:expected_start].encode("utf-8")),
        len(source_text[:expected_end].encode("utf-8")),
    ]
    without_span = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    witness = locator["detail"]["source_witness"]
    assert locator["char_span"] == expected_char_span
    assert locator["byte_span"] == expected_byte_span
    assert locator["detail"]["operation_source_xml_span_status"] == "available"
    assert locator["detail"]["operation_source_xml_quote_match_count"] == 0
    assert locator["detail"]["operation_source_xml_text_container_match_count"] == 1
    assert locator["detail"]["operation_source_xml_text_container_eid"] == "p_1"
    assert locator["detail"]["operation_source_xml_text_container_local_tag"] == "p"
    assert locator["detail"]["char_span_status"] == "operation_source_raw_xml_text_container_scan"
    assert locator["detail"]["byte_span_status"] == "operation_source_raw_xml_text_container_scan_utf8"
    assert witness["artifact_char_span"] == expected_char_span
    assert witness["artifact_byte_span"] == expected_byte_span
    assert witness["artifact_span_status"] == "operation_source_raw_xml_text_container_scan"
    assert witness["artifact_span_match_count"] == 1
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"
    assert payload["hashes"]["content_hash"] == without_span["hashes"]["content_hash"]
    assert payload["hashes"]["derived_state_hash"] == without_span["hashes"]["derived_state_hash"]


def test_operation_source_locator_uses_nearest_eid_ancestor_for_split_quote_container() -> None:
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section eId="sec_1"><p>Section <b>1</b> is replaced with a new duty.</p></section></body></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<section eId="sec_1">')
    expected_end = source_text.index("</section>", expected_start) + len("</section>")

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    assert locator["char_span"] == [expected_start, expected_end]
    assert locator["detail"]["operation_source_xml_span_status"] == "available"
    assert locator["detail"]["operation_source_xml_text_container_eid"] == "sec_1"
    assert locator["detail"]["operation_source_xml_text_container_local_tag"] == "section"
    assert locator["detail"]["operation_source_xml_text_container_ancestor_steps"] == 1
    assert locator["detail"]["source_witness"]["artifact_span_status"] == (
        "operation_source_raw_xml_text_container_scan"
    )


def test_operation_source_locator_uses_sourceline_for_unique_no_eid_split_quote_container() -> None:
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><preamble><formula><blockContainer>
            <block name="insertions">Section <b>1</b> is replaced with a new duty.</block>
          </blockContainer></formula></preamble></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<block name="insertions">')
    expected_end = source_text.index("</block>", expected_start) + len("</block>")

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    assert locator["char_span"] == [expected_start, expected_end]
    assert locator["detail"]["operation_source_xml_span_status"] == "available"
    assert "operation_source_xml_text_container_eid" not in locator["detail"]
    assert locator["detail"]["operation_source_xml_text_container_local_tag"] == "block"
    assert locator["detail"]["operation_source_xml_text_container_span_basis"] == (
        "source_line_and_stable_attrs"
    )
    assert locator["detail"]["char_span_status"] == (
        "operation_source_raw_xml_text_container_sourceline_scan"
    )
    assert locator["detail"]["source_witness"]["artifact_span_status"] == (
        "operation_source_raw_xml_text_container_sourceline_scan"
    )


def test_operation_source_locator_anchors_multiblock_condensed_quote_to_formula() -> None:
    raw_text = (
        "muutetaan\n"
        "                         target Act section 1, lisätään\n"
        "                         target Act section 2 seuraavasti:"
    )
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><preamble>
            <formula name="enactingClause">
              <blockContainer>
                <block name="substitutions"><i>muutetaan</i> target Act section 1,</block>
                <block name="substitutions-originals">as inserted by an earlier Act, plus</block>
              </blockContainer>
              <blockContainer>
                <block name="insertions"><i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i> target Act section 2 seuraavasti:</block>
              </blockContainer>
            </formula>
          </preamble></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<formula name="enactingClause">')
    expected_end = source_text.index("</formula>", expected_start) + len("</formula>")

    payload = build_provision_state_response(
        timelines=_timeline_with_content(_section("A provision duty."), raw_text=raw_text),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    assert locator["char_span"] == [expected_start, expected_end]
    assert locator["detail"]["operation_source_xml_span_status"] == "available"
    assert locator["detail"]["operation_source_xml_quote_match_count"] == 0
    assert locator["detail"]["operation_source_xml_text_sequence_match_count"] == 1
    assert locator["detail"]["operation_source_xml_text_sequence_local_tag"] == "formula"
    assert locator["detail"]["operation_source_xml_text_sequence_span_basis"] == (
        "source_line_and_stable_attrs"
    )
    assert locator["detail"]["char_span_status"] == (
        "operation_source_raw_xml_text_sequence_container_scan"
    )
    assert locator["detail"]["source_witness"]["artifact_span_status"] == (
        "operation_source_raw_xml_text_sequence_container_scan"
    )


def test_operation_source_locator_rejects_duplicate_multiblock_sequence_containers() -> None:
    raw_text = (
        "muutetaan\n"
        "                         target Act section 1, lisätään\n"
        "                         target Act section 2 seuraavasti:"
    )
    formula = """
            <formula name="enactingClause">
              <blockContainer>
                <block name="substitutions"><i>muutetaan</i> target Act section 1,</block>
                <block name="substitutions-originals">as inserted by an earlier Act, plus</block>
              </blockContainer>
              <blockContainer>
                <block name="insertions"><i>lisätään</i> target Act section 2 seuraavasti:</block>
              </blockContainer>
            </formula>
    """
    source_xml = f"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><preamble>{formula}{formula}</preamble></act>
        </akomaNtoso>
        """.encode("utf-8")

    payload = build_provision_state_response(
        timelines=_timeline_with_content(_section("A provision duty."), raw_text=raw_text),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    witness = locator["detail"]["source_witness"]
    assert "char_span" not in locator
    assert "byte_span" not in locator
    assert locator["detail"]["operation_source_xml_span_status"] == (
        "unavailable_operation_source_text_sequence_container_not_unique"
    )
    assert locator["detail"]["operation_source_xml_quote_match_count"] == 0
    assert locator["detail"]["operation_source_xml_text_sequence_match_count"] == 2
    assert witness["artifact_span_status"] == (
        "unavailable_operation_source_text_sequence_container_not_unique"
    )
    assert witness["artifact_span_match_count"] == 2


def test_operation_source_locator_rejects_duplicate_raw_quote_xml_span() -> None:
    quote = "Section 1 is replaced with a new duty."
    source_xml = f"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section><p>{quote}</p><p>{quote}</p></section></body></act>
        </akomaNtoso>
        """.encode("utf-8")

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    witness = locator["detail"]["source_witness"]
    assert locator["artifact_digest"] == hashlib.sha256(source_xml).hexdigest()
    assert locator["artifact_digest_algorithm"] == "sha256"
    assert "char_span" not in locator
    assert "byte_span" not in locator
    assert locator["detail"]["operation_source_xml_span_status"] == (
        "unavailable_operation_source_quote_not_unique"
    )
    assert locator["detail"]["operation_source_xml_quote_match_count"] == 2
    assert witness["artifact_span_status"] == "unavailable_operation_source_quote_not_unique"
    assert witness["artifact_span_match_count"] == 2
    assert witness["digest"] == locator["artifact_digest"]
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"


def test_operation_source_locator_rejects_duplicate_markup_split_quote_containers() -> None:
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section eId="sec_1">
            <p eId="p_1">Section <b>1</b> is replaced with a new duty.</p>
            <p eId="p_2">Section <b>1</b> is replaced with a new duty.</p>
          </section></body></act>
        </akomaNtoso>
        """
    )

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2019/1" else None,
    )

    locator = payload["source_locator"]
    witness = locator["detail"]["source_witness"]
    assert "char_span" not in locator
    assert "byte_span" not in locator
    assert locator["detail"]["operation_source_xml_span_status"] == (
        "unavailable_operation_source_text_container_not_unique"
    )
    assert locator["detail"]["operation_source_xml_quote_match_count"] == 0
    assert locator["detail"]["operation_source_xml_text_container_match_count"] == 2
    assert witness["artifact_span_status"] == (
        "unavailable_operation_source_text_container_not_unique"
    )
    assert witness["artifact_span_match_count"] == 2
    assert source_witness_digest_coverage(witness) == "artifact_and_preview_digest"


def test_lawvm_code_identity_ignores_untracked_files(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(args, **kwargs):
        command = tuple(str(part) for part in args)
        calls.append(command)
        if "rev-parse" in command and "--is-inside-work-tree" in command:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        if "rev-parse" in command and "HEAD" in command:
            return subprocess.CompletedProcess(command, 0, stdout="a" * 40 + "\n", stderr="")
        if "status" in command:
            assert "--untracked-files=no" in command
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command!r}")

    monkeypatch.setattr("lawvm.tools.provision_state.subprocess.run", fake_run)

    identity = _lawvm_code_identity()

    assert identity["git_dirty"] == "false"
    assert identity["build_id"] == "git:" + "a" * 40
    assert any("--untracked-files=no" in call for call in calls)


def test_lawvm_code_identity_marks_tracked_changes_dirty(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        command = tuple(str(part) for part in args)
        if "rev-parse" in command and "--is-inside-work-tree" in command:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        if "rev-parse" in command and "HEAD" in command:
            return subprocess.CompletedProcess(command, 0, stdout="b" * 40 + "\n", stderr="")
        if "status" in command:
            return subprocess.CompletedProcess(command, 0, stdout=" M src/lawvm/tools/provision_state.py\n", stderr="")
        raise AssertionError(f"unexpected subprocess command: {command!r}")

    monkeypatch.setattr("lawvm.tools.provision_state.subprocess.run", fake_run)

    identity = _lawvm_code_identity()

    assert identity["git_dirty"] == "true"
    assert identity["build_id"] == "git:" + "b" * 40 + "+dirty"


def test_operation_source_witness_char_span_uses_raw_text_boundary_trim() -> None:
    payload = build_provision_state_response(
        timelines=_timeline_with_content(
            _section("A provision duty."),
            raw_text="\n  Section 1 is replaced with a new duty.  \n",
        ),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )

    witness = payload["source_locator"]["detail"]["source_witness"]
    assert witness["quote"] == "Section 1 is replaced with a new duty."
    assert witness["quote_char_span"] == [3, 41]
    assert witness["full_raw_text_char_span"] == [3, 41]
    assert witness["quote_hash_semantics"] == "sha256(trimmed full OperationSource.raw_text)"


def test_derived_state_hash_changes_when_temporal_metadata_changes_without_text_change() -> None:
    without_expiry = build_provision_state_response(
        timelines=_timeline(expires=""),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    with_expiry = build_provision_state_response(
        timelines=_timeline(expires="2025-01-01"),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )

    assert without_expiry["hashes"]["content_hash"] == with_expiry["hashes"]["content_hash"]
    assert without_expiry["hashes"]["derived_state_hash"] != with_expiry["hashes"]["derived_state_hash"]


def test_structured_content_hash_changes_when_tree_shape_changes_without_text_change() -> None:
    flat = _section("A provision duty.")
    nested = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.CONTENT, text="A provision duty."),),
    )
    flat_payload = build_provision_state_response(
        timelines=_timeline_with_content(flat),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    nested_payload = build_provision_state_response(
        timelines=_timeline_with_content(nested),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )

    assert flat_payload["text"]["rendered"] == nested_payload["text"]["rendered"]
    assert flat_payload["hashes"]["content_hash"] == nested_payload["hashes"]["content_hash"]
    assert flat_payload["hashes"]["derived_state_hash"] == nested_payload["hashes"]["derived_state_hash"]
    assert flat_payload["hashes"]["structured_content_hash"] != nested_payload["hashes"]["structured_content_hash"]


def test_address_resolution_reports_ambiguous_suffix_without_order_dependent_choice() -> None:
    first = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    second = LegalAddress(path=(("chapter", "2"), ("section", "1")))
    timelines = {
        first: ProvisionTimeline(address=first),
        second: ProvisionTimeline(address=second),
    }

    resolution = resolve_address(timelines, "section:1")

    assert resolution.resolution_status == "ambiguous_address"
    assert resolution.address is None
    assert tuple(str(candidate) for candidate in resolution.candidates) == (
        "chapter:1/section:1",
        "chapter:2/section:1",
    )


def test_query_address_resolution_prefers_unique_live_suffix_over_exact_tombstone() -> None:
    exact = LegalAddress(path=(("section", "125"),))
    qualified = LegalAddress(path=(("part", "6"), ("chapter", "1"), ("section", "125")))
    timelines = {
        exact: ProvisionTimeline(
            address=exact,
            versions=[
                ProvisionVersion(
                    effective="2025-01-01",
                    enacted="2024-12-01",
                    content=None,
                )
            ],
        ),
        qualified: ProvisionTimeline(
            address=qualified,
            versions=[
                ProvisionVersion(
                    effective="2026-01-01",
                    enacted="2025-12-01",
                    content=_section("Live qualified section."),
                )
            ],
        ),
    }

    resolution = resolve_address_for_query(
        timelines,
        "section:125",
        as_of="2026-06-11",
        query_type="in_force",
        territory=None,
    )

    assert resolution.resolution_status == "resolved"
    assert resolution.address == qualified
    assert resolution.mode == "unique_live_suffix_over_exact_tombstone"


def test_query_address_resolution_keeps_exact_when_suffix_live_candidate_is_ambiguous() -> None:
    exact = LegalAddress(path=(("section", "125"),))
    qualified_a = LegalAddress(path=(("part", "6"), ("chapter", "1"), ("section", "125")))
    qualified_b = LegalAddress(path=(("part", "7"), ("chapter", "1"), ("section", "125")))
    timelines = {
        exact: ProvisionTimeline(
            address=exact,
            versions=[ProvisionVersion(effective="2025-01-01", content=None)],
        ),
        qualified_a: ProvisionTimeline(
            address=qualified_a,
            versions=[ProvisionVersion(effective="2026-01-01", content=_section("A."))],
        ),
        qualified_b: ProvisionTimeline(
            address=qualified_b,
            versions=[ProvisionVersion(effective="2026-01-01", content=_section("B."))],
        ),
    }

    resolution = resolve_address_for_query(
        timelines,
        "section:125",
        as_of="2026-06-11",
        query_type="in_force",
        territory=None,
    )

    assert resolution.address == exact
    assert resolution.mode == "exact"


def test_provision_state_response_exposes_lineage_chain_from_migration_events() -> None:
    migration = MigrationEvent(
        event_id="renumber-1",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"), ("section", "1"))),
        to_address=LegalAddress(path=(("chapter", "1"), ("section", "2"))),
        effective="2020-06-01",
        source_statute="2020/2",
    )

    payload = build_provision_state_response(
        timelines=_timeline(),
        migration_events=(migration,),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )

    assert payload["lineage"]["lineage_status"] == "migration_chain"
    assert [entry["text"] for entry in payload["lineage"]["address_chain"]] == [
        "chapter:1/section:1",
        "chapter:1/section:2",
    ]
    assert payload["lineage"]["migration_event_count_considered"] == 1
    assert len(payload["lineage"]["fingerprint"]) == 64
    assert payload["lineage"]["fingerprint_algorithm"] == "sha256"
    assert "excluded from derived_state_hash" in payload["lineage"]["fingerprint_semantics"]

    address = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    legacy_lineage = {
        "lineage_status": payload["lineage"]["lineage_status"],
        "address_chain": payload["lineage"]["address_chain"],
        "migration_event_count_considered": payload["lineage"]["migration_event_count_considered"],
    }
    with_fingerprint = _hash_payload(
        payload_status="selected",
        statute_id="2000/1",
        jurisdiction="fi",
        query=payload["query"],
        address=address,
        lineage=payload["lineage"],
        version=None,
        content_hash="",
    )
    without_fingerprint = _hash_payload(
        payload_status="selected",
        statute_id="2000/1",
        jurisdiction="fi",
        query=payload["query"],
        address=address,
        lineage=legacy_lineage,
        version=None,
        content_hash="",
    )
    assert with_fingerprint["derived_state_hash"] == without_fingerprint["derived_state_hash"]


def test_provision_state_response_uses_base_source_locator_for_sourceless_base_version() -> None:
    address = LegalAddress(path=(("section", "1"),))
    content = _section("Base duty.")
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    content=content,
                    content_hash=irnode_content_hash(content),
                )
            ],
        )
    }
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section eId="sec_1"><num>1 \xc2\xa7</num><content>Base duty.</content></section></body></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<section eId="sec_1">')
    expected_end = source_text.index("</section>", expected_start) + len("</section>")
    expected_char_span = [expected_start, expected_end]
    expected_byte_span = [
        len(source_text[:expected_start].encode("utf-8")),
        len(source_text[:expected_end].encode("utf-8")),
    ]
    expected_digest = hashlib.sha256(source_xml).hexdigest()

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2000/1" else None,
    )

    assert payload["source"] is None
    assert payload["source_locator_status"] == "canonical_document_locator"
    assert payload["source_locator"]["artifact_kind"] == "base_statute_xml"
    assert payload["source_locator"]["artifact_digest"] == expected_digest
    assert payload["source_locator"]["artifact_digest_algorithm"] == "sha256"
    assert payload["source_locator"]["document_uri"] == "finlex://sd/2000/1/fin/main.xml"
    assert payload["source_locator"]["structural_path"] == "lawvm-target:section:1"
    assert payload["source_locator"]["xpath"].startswith("//*[local-name()='body']")
    xml = etree.fromstring(source_xml)
    matches = xml.xpath(payload["source_locator"]["xpath"])
    assert isinstance(matches, list)
    assert len(matches) == 1
    assert "section" in payload["source_locator"]["xpath"]
    assert payload["source_locator"]["detail"]["xpath"] == payload["source_locator"]["xpath"]
    assert payload["source_locator"]["detail"]["artifact_digest"] == expected_digest
    assert payload["source_locator"]["detail"]["artifact_digest_algorithm"] == "sha256"
    assert payload["source_locator"]["detail"]["artifact_digest_status"] == "source_xml_bytes_sha256"
    assert payload["source_locator"]["detail"]["xpath_status"] == "finlex_structural_xpath_candidate"
    assert payload["source_locator"]["detail"]["target_xpath_candidate_status"] == (
        "finlex_structural_xpath_candidate"
    )
    assert payload["source_locator"]["char_span"] == expected_char_span
    assert payload["source_locator"]["byte_span"] == expected_byte_span
    assert payload["source_locator"]["detail"]["char_span"] == expected_char_span
    assert payload["source_locator"]["detail"]["char_span_status"] == (
        "finlex_raw_xml_eid_element_scan"
    )
    assert payload["source_locator"]["detail"]["byte_span"] == expected_byte_span
    assert payload["source_locator"]["detail"]["byte_span_status"] == (
        "finlex_raw_xml_eid_element_scan_utf8"
    )
    assert payload["source_locator"]["detail"]["source_xml_span_status"] == "available"
    assert payload["source_locator"]["detail"]["source_xml_eid"] == "sec_1"
    assert payload["source_locator"]["detail"]["source_xml_local_tag"] == "section"
    assert payload["source_locator"]["detail"]["source_witness_status"] == (
        "unavailable_no_operation_source_raw_text"
    )
    assert "source_witness" not in payload["source_locator"]["detail"]


def test_base_source_locator_xml_spans_do_not_affect_state_hashes() -> None:
    address = LegalAddress(path=(("section", "1"),))
    content = _section("Base duty.")
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    content=content,
                    content_hash=irnode_content_hash(content),
                )
            ],
        )
    }
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><section eId="sec_1"><num>1 \xc2\xa7</num><content>Base duty.</content></section></body></act>
        </akomaNtoso>
        """
    )

    without_span = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
    )
    with_span = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2000/1" else None,
    )

    assert "char_span" not in without_span["source_locator"]
    assert without_span["source_locator"]["detail"]["byte_span_status"] == "unavailable_initial_surface"
    assert with_span["source_locator"]["char_span"]
    assert with_span["source_locator"]["byte_span"]
    assert with_span["source_locator"]["detail"]["hash_role"] == "excluded_from_derived_state_hash"
    assert with_span["hashes"]["content_hash"] == without_span["hashes"]["content_hash"]
    assert with_span["hashes"]["derived_state_hash"] == without_span["hashes"]["derived_state_hash"]


def test_base_source_locator_char_span_falls_back_to_finlex_eid() -> None:
    address = LegalAddress(path=(("section", "1"),))
    content = _section("Base duty.")
    timelines = {
        address: ProvisionTimeline(
            address=address,
            versions=[
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    content=content,
                    content_hash=irnode_content_hash(content),
                )
            ],
        )
    }
    source_xml = (
        b"""
        <akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <act><body><hcontainer eId="container_1"><section eId="sec_1"><num>1 \xc2\xa7</num><content>Base duty.</content></section></hcontainer></body></act>
        </akomaNtoso>
        """
    )
    source_text = source_xml.decode("utf-8")
    expected_start = source_text.index('<section eId="sec_1">')
    expected_end = source_text.index("</section>", expected_start) + len("</section>")

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="2021-01-01",
        source_xml_provider=lambda sid: source_xml if sid == "2000/1" else None,
    )

    locator = payload["source_locator"]
    assert locator["char_span"] == [expected_start, expected_end]
    assert locator["detail"]["source_xml_span_status"] == "available"
    assert locator["detail"]["source_xml_span_match_basis"] == "fallback_eid"
    assert locator["detail"]["source_xml_xpath_match_count"] == 0
    assert locator["detail"]["source_xml_eid"] == "sec_1"
    assert "fallback eId matched one element" in locator["detail"]["char_span_basis"]


def test_public_resolve_provision_state_reports_unsupported_jurisdiction_without_replay() -> None:
    payload = resolve_provision_state(
        statute_id="ukpga/2000/1",
        jurisdiction="uk",
        provision="section:1",
        as_of="2024-01-01",
    )

    assert payload["schema"] == "lawvm.provision_state.v1"
    assert payload["spec_version"] == "0.3"
    assert payload["provision_status"] == "unsupported_jurisdiction"
    assert payload["supported_jurisdictions"] == ["fi"]


def test_build_provision_state_response_rejects_invalid_as_of() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of="not-a-date",
    )

    assert payload["provision_status"] == "invalid_query"
    assert payload["diagnostic"]["code"] == "LAWVM_PROVISION_AS_OF_INVALID"
    assert payload["diagnostic"]["field"] == "as_of"
    assert payload["source_locator_status"] == "unavailable_invalid_query"
    assert payload["selection"] is None
    assert payload["resolved_address"] is None
    assert len(payload["hashes"]["derived_state_hash"]) == 64


def test_build_provision_state_response_rejects_whitespace_wrapped_as_of() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1",
        as_of=" 2021-01-01 ",
    )

    assert payload["provision_status"] == "invalid_query"
    assert payload["diagnostic"]["code"] == "LAWVM_PROVISION_AS_OF_INVALID"
    assert payload["diagnostic"]["message"] == (
        "as_of must be exactly an ISO date in YYYY-MM-DD form"
    )
    assert payload["selection"] is None
    assert "text" not in payload


def test_public_resolve_provision_state_rejects_invalid_as_of_before_replay() -> None:
    payload = resolve_provision_state(
        statute_id="2023/703",
        jurisdiction="fi",
        provision="section:9",
        as_of="2026-99-99",
    )

    assert payload["provision_status"] == "invalid_query"
    assert payload["diagnostic"]["code"] == "LAWVM_PROVISION_AS_OF_INVALID"
    assert payload["diagnostic"]["message"] == (
        "as_of must be a real calendar date in YYYY-MM-DD form"
    )
    assert "text" not in payload
    assert payload["source_locator_status"] == "unavailable_invalid_query"


def test_public_resolve_provision_state_rejects_finnish_prose_selector_before_replay() -> None:
    payload = resolve_provision_state(
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="127 a §",
        as_of="2024-06-01",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["spec_version"] == "0.3"
    assert payload["diagnostic"]["code"] == "FI_PROVISION_SELECTOR_UNSUPPORTED_PROSE_NOTATION"
    assert payload["diagnostic"]["suggestions"] == ["section:127a"]
    assert payload["source_locator_status"] == "unavailable_invalid_provision"
    assert len(payload["hashes"]["derived_state_hash"]) == 64


def test_public_resolve_provision_state_rejects_finnish_hybrid_selector_before_replay() -> None:
    payload = resolve_provision_state(
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127 a §",
        as_of="2024-06-01",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == "FI_PROVISION_SELECTOR_MALFORMED_HYBRID"
    assert payload["diagnostic"]["suggestions"] == ["section:127a"]


def test_public_resolve_provision_state_rejects_finnish_suffix_as_subsection() -> None:
    payload = resolve_provision_state(
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127/subsection:a",
        as_of="2024-06-01",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == "FI_PROVISION_SELECTOR_SUFFIX_AS_SUBSECTION"
    assert payload["diagnostic"]["suggestions"] == ["section:127a"]


def test_public_resolve_provision_state_rejects_spaced_finnish_section_label() -> None:
    payload = resolve_provision_state(
        statute_id="2021/728",
        jurisdiction="fi",
        provision="section:2 d",
        as_of="2026-01-02",
        query_type="in_force",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "FI_PROVISION_SELECTOR_NON_CANONICAL_SECTION_LABEL"
    )
    assert payload["diagnostic"]["suggestions"] == ["section:2d"]
    assert payload["source_locator_status"] == "unavailable_invalid_provision"


def test_build_provision_state_response_rejects_spaced_section_label_in_path() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2021/728",
        jurisdiction="fi",
        provision="chapter:1/section:2 d",
        as_of="2026-01-02",
        query_type="in_force",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "FI_PROVISION_SELECTOR_NON_CANONICAL_SECTION_LABEL"
    )
    assert payload["diagnostic"]["suggestions"] == ["section:2d"]


def test_build_provision_state_response_rejects_noncanonical_selector_whitespace() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision=" section: 1 ",
        as_of="2021-01-01",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE"
    )
    assert payload["diagnostic"]["suggestions"] == ["section:1"]
    assert payload["selection"] is None
    assert "text" not in payload


def test_public_resolve_provision_state_rejects_leading_trailing_selector_whitespace() -> None:
    payload = resolve_provision_state(
        statute_id="2021/618",
        jurisdiction="fi",
        provision=" section:47 ",
        as_of="2026-01-02",
        query_type="in_force",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE"
    )
    assert payload["diagnostic"]["suggestions"] == ["section:47"]
    assert payload["resolved_address"] is None
    assert payload["selection"] is None


def test_public_resolve_provision_state_rejects_noncanonical_selector_before_replay() -> None:
    payload = resolve_provision_state(
        statute_id="2023/703",
        jurisdiction="fi",
        provision="chapter:2 /section:9",
        as_of="2026-06-02",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE"
    )
    assert payload["diagnostic"]["suggestions"] == ["chapter:2/section:9"]
    assert payload["source_locator_status"] == "unavailable_invalid_provision"


def test_provision_state_cli_invalid_selector_prints_diagnostic_and_exits_2(capsys) -> None:
    args = SimpleNamespace(
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127 a §",
        as_of="2024-06-01",
        query_type="governing",
        territory=None,
        include_ir=False,
    )

    with pytest.raises(SystemExit) as raised:
        main(args)

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "ERROR: invalid --provision 'section:127 a §'" in captured.err
    assert "help: try 'section:127a'" in captured.err
    payload = json.loads(captured.out)
    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == "FI_PROVISION_SELECTOR_MALFORMED_HYBRID"


def test_provision_state_cli_noncanonical_selector_prints_suggestion_and_exits_2(capsys) -> None:
    args = SimpleNamespace(
        statute_id="2023/703",
        jurisdiction="fi",
        provision="section: 9",
        as_of="2026-06-02",
        query_type="governing",
        territory=None,
        include_ir=False,
    )

    with pytest.raises(SystemExit) as raised:
        main(args)

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "ERROR: invalid --provision 'section: 9'" in captured.err
    assert "help: try 'section:9'" in captured.err
    payload = json.loads(captured.out)
    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "LAWVM_PROVISION_SELECTOR_NON_CANONICAL_WHITESPACE"
    )


def test_provision_state_cli_spaced_section_label_exits_2(capsys) -> None:
    args = SimpleNamespace(
        statute_id="2021/728",
        jurisdiction="fi",
        provision="section:2 d",
        as_of="2026-01-02",
        query_type="in_force",
        territory=None,
        include_ir=False,
    )

    with pytest.raises(SystemExit) as raised:
        main(args)

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "ERROR: invalid --provision 'section:2 d'" in captured.err
    assert "help: try 'section:2d'" in captured.err
    payload = json.loads(captured.out)
    assert payload["provision_status"] == "invalid_address"
    assert payload["diagnostic"]["code"] == (
        "FI_PROVISION_SELECTOR_NON_CANONICAL_SECTION_LABEL"
    )


def test_provision_state_cli_invalid_as_of_prints_diagnostic_and_exits_2(capsys) -> None:
    args = SimpleNamespace(
        statute_id="2023/703",
        jurisdiction="fi",
        provision="section:9",
        as_of="not-a-date",
        query_type="governing",
        territory=None,
        include_ir=False,
    )

    with pytest.raises(SystemExit) as raised:
        main(args)

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "ERROR: invalid --as-of: as_of must be exactly an ISO date" in captured.err
    payload = json.loads(captured.out)
    assert payload["provision_status"] == "invalid_query"
    assert payload["diagnostic"]["code"] == "LAWVM_PROVISION_AS_OF_INVALID"


def test_provision_state_cli_address_not_found_prints_nearby_help(
    capsys,
    monkeypatch,
) -> None:
    actual = LegalAddress(path=(("chapter", "6"), ("section", "127 a")))
    payload = build_provision_state_response(
        timelines={actual: ProvisionTimeline(address=actual)},
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127a",
        as_of="2024-06-01",
    )

    def fake_resolve_provision_state(**kwargs):
        return payload

    monkeypatch.setattr(
        "lawvm.provision_state.resolve_provision_state",
        fake_resolve_provision_state,
    )
    args = SimpleNamespace(
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127a",
        as_of="2024-06-01",
        query_type="governing",
        territory=None,
        include_ir=False,
    )

    main(args)

    captured = capsys.readouterr()
    assert "nearest materialized addresses include: chapter:6/section:127 a" in captured.err
    emitted = json.loads(captured.out)
    assert emitted["provision_status"] == "address_not_found"


def test_provision_state_path_parser_rejects_malformed_segments() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:1/not-a-segment",
        as_of="2021-01-01",
    )

    assert payload["provision_status"] == "invalid_address"
    assert payload["resolved_address"] is None
    assert payload["address_candidates"] == []


def test_provision_state_absent_finnish_section_suggests_real_nearby_address_only() -> None:
    actual = LegalAddress(path=(("chapter", "6"), ("section", "127 a")))
    timelines = {actual: ProvisionTimeline(address=actual)}

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="1992/1535",
        jurisdiction="fi",
        provision="section:127a",
        as_of="2024-06-01",
    )

    assert payload["provision_status"] == "address_not_found"
    assert payload["resolved_address"] is None
    assert payload["address_candidates"] == []
    assert payload["diagnostic"]["code"] == "LAWVM_PROVISION_ADDRESS_NOT_FOUND"
    assert payload["diagnostic"]["suggestion_status"] == "non_authoritative_query_help_only"
    assert payload["diagnostic"]["nearby_address_candidates"] == [
        {
            "path": [
                {"kind": "chapter", "label": "6"},
                {"kind": "section", "label": "127 a"},
            ],
            "special": None,
            "text": "chapter:6/section:127 a",
        }
    ]


def test_resolve_address_nearby_suggestions_do_not_resolve_or_rewrite_query() -> None:
    actual = LegalAddress(path=(("section", "127 a"),))
    timelines = {actual: ProvisionTimeline(address=actual)}

    resolution = resolve_address(timelines, "section:127a")

    assert resolution.resolution_status == "address_not_found"
    assert resolution.address is None
    assert resolution.timeline is None
    assert resolution.candidates == ()
    assert resolution.suggestions == (actual,)


def test_provision_state_absent_numeric_section_suggests_close_real_sections() -> None:
    near = LegalAddress(path=(("section", "128"),))
    far = LegalAddress(path=(("section", "140"),))
    timelines = {
        near: ProvisionTimeline(address=near),
        far: ProvisionTimeline(address=far),
    }

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:130",
        as_of="2021-01-01",
    )

    assert payload["provision_status"] == "address_not_found"
    assert [
        candidate["text"]
        for candidate in payload["diagnostic"]["nearby_address_candidates"]
    ] == ["section:128"]


# --- timeline-integrity surfacing ---------------------------------------------
# A replay abort / broken compile fold must never produce clean-looking answers.
# Break evidence is classified by lawvm.tools.timeline_integrity; the seam marks
# affected responses with status="timeline_unverified" plus a typed
# `timeline_broken_at` marker. Responses WITHOUT break evidence stay
# byte-identical (conditional hash member, same discipline as `expiry`).

from lawvm.core.phase_result import Finding
from lawvm.tools.timeline_integrity import (
    TimelineBreak,
    attach_effective_dates,
    timeline_breaks_from_findings,
)


def _occupancy_finding(*, non_primary: bool = False) -> Finding:
    detail: dict[str, object] = {
        "target_label": "29e",
        "current_occupancy": "absent",
        "allowed_from": ["substantive", "tombstone"],
    }
    if non_primary:
        detail["allowed_non_primary"] = True
    return Finding(
        kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
        role="observation",
        stage="apply",
        source_statute="2025/1382",
        detail=detail,
        blocking=False,
    )


def _statute_break(effective: str = "2020-06-01") -> TimelineBreak:
    return TimelineBreak(
        amendment_id="2019/500",
        diagnostic_code="APPLY.OCCUPANCY_POLICY_VIOLATION",
        scope="statute",
        target_unit_kind="section",
        target_section="9",
        effective=effective,
    )


def test_timeline_break_classifier_known_occupancy_violation_is_address_scoped() -> None:
    breaks = timeline_breaks_from_findings([_occupancy_finding()])
    assert len(breaks) == 1
    assert breaks[0].scope == "address"
    assert breaks[0].amendment_id == "2025/1382"
    assert breaks[0].diagnostic_code == "APPLY.OCCUPANCY_POLICY_VIOLATION"
    assert breaks[0].target_section == "29e"


def test_timeline_break_classifier_targetless_occupancy_violation_is_statute_scoped() -> None:
    finding = _occupancy_finding()
    finding = Finding(
        kind=finding.kind,
        role=finding.role,
        stage=finding.stage,
        source_statute=finding.source_statute,
        detail={
            key: value
            for key, value in finding.detail.items()
            if key != "target_label"
        },
        blocking=finding.blocking,
    )

    breaks = timeline_breaks_from_findings([finding])

    assert len(breaks) == 1
    assert breaks[0].scope == "statute"
    assert breaks[0].target_section == ""


def test_timeline_break_classifier_occupancy_extracts_chapter_from_context_label() -> None:
    base = _occupancy_finding()
    finding = Finding(
        kind=base.kind,
        role=base.role,
        stage=base.stage,
        source_statute=base.source_statute,
        detail={**base.detail, "ctx_label": "[2022/86] INSERT 11 luku 4b §"},
        blocking=base.blocking,
    )

    breaks = timeline_breaks_from_findings([finding])

    assert len(breaks) == 1
    assert breaks[0].scope == "address"
    assert breaks[0].target_chapter == "11"
    assert breaks[0].target_section == "29e"


def test_timeline_break_classifier_skips_allowed_non_primary_occupancy_note() -> None:
    assert timeline_breaks_from_findings([_occupancy_finding(non_primary=True)]) == ()


def test_timeline_break_classifier_failed_operation_is_address_scoped() -> None:
    finding = Finding(
        kind="APPLY.FAILED_OPERATION",
        role="obligation",
        stage="process_muutoslaki",
        source_statute="",
        detail={
            "amendment_id": "2022/378",
            "reason_code": "section_not_found",
            "target_unit_kind": "section",
            "target_section": "16",
            "target_chapter": None,
        },
        blocking=True,
    )
    breaks = timeline_breaks_from_findings([finding])
    assert len(breaks) == 1
    assert breaks[0].scope == "address"
    assert breaks[0].amendment_id == "2022/378"
    assert breaks[0].target_section == "16"
    assert breaks[0].reason == "section_not_found"


def test_timeline_break_classifier_timeline_fatal_detail_hook_is_statute_scoped() -> None:
    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="apply",
        source_statute="2024/9",
        detail={"timeline_fatal": True},
        blocking=False,
    )
    breaks = timeline_breaks_from_findings([finding])
    assert len(breaks) == 1
    assert breaks[0].scope == "statute"
    assert breaks[0].amendment_id == "2024/9"


def test_timeline_break_effective_dates_attach_from_lineage() -> None:
    breaks = attach_effective_dates(
        timeline_breaks_from_findings([_occupancy_finding()]),
        [{"statute_id": "2025/1382", "effective_date": "2026-01-01"}],
    )
    assert breaks[0].effective == "2026-01-01"


def test_provision_state_without_breaks_is_byte_identical() -> None:
    base = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    explicit_empty = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(),
    )
    assert base == explicit_empty
    assert "timeline_broken_at" not in base
    assert "timeline_integrity" not in base


def test_selected_response_exposes_recovery_diagnostics_without_hash_change() -> None:
    finding = Finding(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        role="obligation",
        stage="coverage_analysis",
        source_statute="2019/1",
        blocking=True,
        detail={
            "message": "chapter-level INSERT plan proceeded with degraded confidence",
            "uncovered_count": 11,
            "total_units": 21,
            "uncov_ratio": 0.5238,
            "confidence": 0.75,
            "signals": ["new_chapter_insert"],
        },
    )

    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        findings=(finding, finding),
    )

    assert payload["provision_status"] == "selected"
    assert payload["text"]["available"] is True
    assert payload["diagnostics"] == [
        {
            "code": "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
            "role": "obligation",
            "stage": "coverage_analysis",
            "source_statute": "2019/1",
            "finding_blocking": True,
            "seam_blocking": False,
            "detail": {
                "message": "chapter-level INSERT plan proceeded with degraded confidence",
                "uncovered_count": 11,
                "total_units": 21,
                "uncov_ratio": 0.5238,
                "confidence": 0.75,
                "signals": ("new_chapter_insert",),
            },
            "hash_role": "excluded_from_derived_state_hash",
        }
    ]
    assert payload["hashes"]["derived_state_hash"] == clean["hashes"]["derived_state_hash"]


def test_selected_response_recovery_diagnostics_are_source_and_target_scoped() -> None:
    matching = Finding(
        kind="APPLY.UNCOVERED_BODY_RECOVERY",
        role="obligation",
        stage="apply",
        source_statute="2019/1",
        blocking=True,
        detail={
            "message": "Uncovered-body insertion supplement was used.",
            "op_id": "uncovered_insert_1",
            "target_unit_kind": "section",
            "target_norm": "1",
            "target_chapter": "1",
            "barrier_code": "APPLY.UNCOVERED_BODY_RECOVERY",
        },
    )
    wrong_target = Finding(
        kind="APPLY.UNCOVERED_BODY_RECOVERY",
        role="obligation",
        stage="apply",
        source_statute="2019/1",
        blocking=True,
        detail={
            "message": "Uncovered-body insertion supplement was used.",
            "op_id": "uncovered_insert_2",
            "target_unit_kind": "section",
            "target_norm": "2",
            "target_chapter": "1",
            "barrier_code": "APPLY.UNCOVERED_BODY_RECOVERY",
        },
    )
    wrong_source = Finding(
        kind="COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED",
        role="obligation",
        stage="coverage_analysis",
        source_statute="2019/2",
        blocking=True,
        detail={"message": "wrong source"},
    )

    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        findings=(matching, wrong_target, wrong_source),
    )

    assert [item["code"] for item in payload["diagnostics"]] == [
        "APPLY.UNCOVERED_BODY_RECOVERY"
    ]
    assert payload["diagnostics"][0]["detail"]["target_norm"] == "1"


def test_statute_scoped_break_blocks_post_break_query() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    assert payload["provision_status"] == "timeline_unverified"
    assert payload["timeline_broken_at"] == {
        "amendment_id": "2019/500",
        "diagnostic_code": "APPLY.OCCUPANCY_POLICY_VIOLATION",
    }
    assert payload["timeline_integrity"]["blocking"] is True
    # Content must be withheld: an unproven timeline cannot assert text-state.
    assert payload["version"] is None
    assert payload["text"]["available"] is False
    assert payload["hashes"]["content_hash"] == ""
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    assert payload["hashes"]["derived_state_hash"] != clean["hashes"]["derived_state_hash"]


def test_statute_scoped_break_after_as_of_serves_with_warning_marker() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2022-01-01"),),
    )
    # The pre-break timeline is proven: serve the answer, keep the marker visible.
    assert payload["provision_status"] == "selected"
    assert payload["text"]["available"] is True
    assert payload["timeline_broken_at"]["amendment_id"] == "2019/500"
    assert payload["timeline_integrity"]["blocking"] is False
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    # The warning is part of the hashed state (a consumer pin must notice it).
    assert payload["hashes"]["derived_state_hash"] != clean["hashes"]["derived_state_hash"]


def test_undated_break_is_conservatively_blocking() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break(""),),
    )
    assert payload["provision_status"] == "timeline_unverified"
    assert payload["timeline_integrity"]["blocking"] is True


def test_address_scoped_break_only_affects_matching_target() -> None:
    failed_op_break = TimelineBreak(
        amendment_id="2022/378",
        diagnostic_code="APPLY.FAILED_OPERATION",
        scope="address",
        target_unit_kind="section",
        target_section="1",
        effective="2020-06-01",
        reason="section_not_found",
    )
    matching = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(failed_op_break,),
    )
    assert matching["provision_status"] == "timeline_unverified"
    assert matching["timeline_broken_at"]["amendment_id"] == "2022/378"

    other_target = TimelineBreak(
        amendment_id="2022/378",
        diagnostic_code="APPLY.FAILED_OPERATION",
        scope="address",
        target_unit_kind="section",
        target_section="99",
        effective="2020-06-01",
        reason="section_not_found",
    )
    unaffected = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(other_target,),
    )
    clean = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
    )
    # Non-matching targets must leave the response byte-identical.
    assert unaffected == clean


def test_blocking_break_overrides_unresolved_address_status() -> None:
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="section:404",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    # "address_not_found" would read as a legal fact ("no such provision"), but
    # the breaking amendment could have created the address: block instead.
    assert payload["provision_status"] == "timeline_unverified"
    assert payload["timeline_integrity"]["resolution_status"] == "address_not_found"
    assert payload["timeline_broken_at"]["amendment_id"] == "2019/500"


def test_timeline_integrity_flag_off_restores_prior_behavior(monkeypatch) -> None:
    monkeypatch.setenv("LAWVM_ENABLE_TIMELINE_INTEGRITY_SURFACING", "0")
    payload = build_provision_state_response(
        timelines=_timeline(),
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:1",
        as_of="2021-01-01",
        timeline_breaks=(_statute_break("2020-06-01"),),
    )
    assert payload["provision_status"] == "selected"
    assert "timeline_broken_at" not in payload


def test_provision_state_materializes_later_child_overlay_under_temporary_parent() -> None:
    parent_address = LegalAddress(path=(("chapter", "1"), ("section", "7")))
    child_1_address = LegalAddress(path=(*parent_address.path, ("subsection", "1")))
    child_2_address = LegalAddress(path=(*parent_address.path, ("subsection", "2")))
    base_section = _numbered_section(
        "7",
        (
            _subsection("1", "base child 1"),
            _subsection("2", "base child 2"),
        ),
    )
    base = IRStatute(
        statute_id="2000/1",
        title="Base",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(base_section,)),),
        ),
    )
    temporary_parent = _numbered_section(
        "7",
        (
            _subsection("1", "temporary child 1"),
            _subsection("2", "temporary child 2"),
        ),
    )
    timelines = {
        parent_address: ProvisionTimeline(
            address=parent_address,
            versions=[
                ProvisionVersion(
                    effective="2010-01-01",
                    enacted="2010-01-01",
                    expires="2020-01-01",
                    content=temporary_parent,
                    content_hash=irnode_content_hash(temporary_parent),
                )
            ],
        ),
        child_1_address: ProvisionTimeline(
            address=child_1_address,
            versions=[
                ProvisionVersion(
                    effective="2005-01-01",
                    enacted="2005-01-01",
                    content=_subsection("1", "older child 1"),
                )
            ],
        ),
        child_2_address: ProvisionTimeline(
            address=child_2_address,
            versions=[
                ProvisionVersion(
                    effective="2015-01-01",
                    enacted="2015-01-01",
                    content=_subsection("2", "later child 2"),
                )
            ],
        ),
    }

    payload = build_provision_state_response(
        timelines=timelines,
        statute_id="2000/1",
        jurisdiction="fi",
        provision="chapter:1/section:7",
        as_of="2016-01-01",
        base=base,
        include_ir=True,
    )

    rendered = payload["text"]["rendered"]
    assert payload["provision_status"] == "selected"
    assert "temporary child 1" in rendered
    assert "older child 1" not in rendered
    assert "temporary child 2" not in rendered
    assert "later child 2" in rendered
    assert payload["content_derivation"]["mode"] == "pit_materialized_descendant_overlays"
    assert payload["hashes"]["content_hash"] == payload["content_derivation"][
        "materialized_content_hash"
    ]


# --- live-corpus specimen regression -------------------------------------------
# Consumer-reported specimen: 2014/1429 replay records an occupancy violation at
# 2025/1382 (timeline break), yet the seam used to serve clean-looking answers
# (status=selected / address_not_found with empty fields). The assertion is
# CONSISTENCY between recorded break evidence and the surfaced marker, so this
# test stays green if/when the underlying replay break is fixed: with evidence
# the response must be marked; without evidence it must be clean.

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


@pytest.fixture(scope="module")
def live_provision_state_runtime_for_statute():
    from lawvm.provision_state import compile_provision_state_runtime

    runtimes = {}

    def runtime_for(statute_id: str):
        runtime = runtimes.get(statute_id)
        if runtime is None:
            runtime = compile_provision_state_runtime(statute_id=statute_id)
            runtimes[statute_id] = runtime
        return runtime

    return runtime_for


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1992_1535_section_125_prefers_live_qualified_section(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1992/1535").resolve(
        jurisdiction="fi",
        provision="section:125",
        as_of="2026-06-11",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert payload["resolved_address"]["text"] == "part:6/chapter:1/section:125"
    # The bare ``section:125`` query now resolves cleanly by unique suffix to
    # the live part-qualified section.  Earlier, an injected pure-kumotaan
    # REPEAL lost its enclosing part scope and landed on a bare ``section:125``
    # address, manufacturing a phantom tombstone timeline that shadowed the
    # live ``part:6/chapter:1/section:125`` one.  The resolver had to break
    # that tie via the ``unique_live_suffix_over_exact_tombstone`` seam.  With
    # the repeal now targeting the resolved part-scoped path, the phantom
    # tombstone is gone, leaving a single timeline ending in ``section:125`` —
    # so a plain unique-suffix match is the correct, more accurate mode.
    assert payload["address_match"]["mode"] == "unique_suffix"
    assert payload["version"]["content_state"] == "live"
    assert payload["source"]["statute_id"] == "2025/1141"
    assert "Työtulovähennys" in payload["text"]["rendered"]


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1997_1412_section_11_drops_expired_temporary_items(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1997/1412").resolve(
        jurisdiction="fi",
        provision="section:11",
        as_of="2026-06-11",
        query_type="in_force",
    )

    rendered = payload["text"]["rendered"]
    assert payload["provision_status"] == "selected"
    assert "väliaikaisesta epidemiakorvauksesta" not in rendered
    assert "lapsilisälain (796/1992) 7 §:n 5 momentissa" not in rendered


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2009_273_section_10_drops_carried_old_subsection_text(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("2009/273").resolve(
        jurisdiction="fi",
        provision="section:10",
        as_of="2026-06-10",
        query_type="in_force",
    )

    rendered = payload["text"]["rendered"]
    assert payload["provision_status"] == "selected"
    assert "15 a §:ssä tarkoitettu seuraamuslautakunta" in rendered
    assert "15 §:ssä tarkoitettu uhkasakkolautakunta" not in rendered
    assert "hallintolainkäyttölaissa (586/1996)" not in rendered


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2016_258_section_7_exposes_child_overlay_in_parent_text(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("2016/258").resolve(
        jurisdiction="fi",
        provision="section:7",
        as_of="2021-12-31",
    )

    assert payload["provision_status"] == "selected"
    rendered = payload["text"]["rendered"]
    assert "0,70 euroa sivulta" in rendered
    assert "1,40 euroa sivulta" in rendered
    assert "23 euroa levyltä" in rendered
    assert "0,60 euroa sivulta" not in rendered
    assert payload["content_derivation"]["mode"] == "pit_materialized_descendant_overlays"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2023_703_section_9_exposes_operation_source_witness(
    live_provision_state_runtime_for_statute,
) -> None:
    """Selected amended versions should carry the source johtolause witness."""
    payload = live_provision_state_runtime_for_statute("2023/703").resolve(
        jurisdiction="fi",
        provision="section:9",
        as_of="2026-06-02",
    )

    assert payload["provision_status"] == "selected"
    assert payload["source"]["statute_id"] == "2026/376"
    locator = payload["source_locator"]
    assert locator["detail"]["source_witness_status"] == (
        "operation_source_raw_text_available"
    )
    witness = locator["detail"]["source_witness"]
    assert witness["kind"] == "operation_source_raw_text"
    assert "lisätään" in witness["quote"]
    assert "9 §:ään" in witness["quote"]
    assert locator["quote_hash"] == witness["quote_hash"]
    assert locator["detail"]["hash_role"] == "excluded_from_derived_state_hash"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1972_66_injected_repeal_exposes_operation_source_witness(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1972/66").resolve(
        jurisdiction="fi",
        provision="chapter:4/section:27a",
        as_of="2020-12-11",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert payload["version"]["content_state"] == "tombstone"
    assert payload["source"]["statute_id"] == "1982/684"
    assert payload["source"]["title"] == "Laki kansanterveyslain muuttamisesta"
    assert payload["source"]["enacted"] == "1982-09-17"
    locator = payload["source_locator"]
    detail = locator["detail"]
    assert detail["source_witness_status"] == "operation_source_raw_text_available"
    witness = detail["source_witness"]
    assert witness["kind"] == "operation_source_raw_text"
    assert "kumotaan" in witness["quote"]
    assert "4 luvun otsikko 27-39 §" in witness["quote"]
    assert locator["quote_hash"] == witness["quote_hash"]
    assert detail["operation_source_xml_span_status"] == "available"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_1996_1128_subsection_repeal_placeholder_is_tombstone_with_witness(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1996/1128").resolve(
        jurisdiction="fi",
        provision="section:4/subsection:4",
        as_of="2021-01-01",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert payload["version"]["content_state"] == "tombstone"
    assert payload["text"]["available"] is False
    assert payload["hashes"]["content_hash"] == ""
    assert payload["hashes"]["structured_content_hash"] == ""
    assert payload["source"]["statute_id"] == "2019/1396"
    assert payload["source"]["title"] == (
        "Laki lasten kotihoidon ja yksityisen hoidon tuesta annetun lain muuttamisesta"
    )
    locator = payload["source_locator"]
    detail = locator["detail"]
    assert detail["source_witness_status"] == "operation_source_raw_text_available"
    witness = detail["source_witness"]
    assert "kumotaan" in witness["quote"]
    assert "4 §:n 4 momentti" in witness["quote"]


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1972_66_snapshot_repeal_placeholder_exposes_source_witness(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1972/66").resolve(
        jurisdiction="fi",
        provision="chapter:4/section:27",
        as_of="2020-12-11",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert payload["version"]["content_state"] == "tombstone"
    assert payload["text"]["available"] is False
    assert payload["source"]["statute_id"] == "2010/1327"
    locator = payload["source_locator"]
    detail = locator["detail"]
    assert detail["source_witness_status"] == "operation_source_raw_text_available"
    witness = detail["source_witness"]
    assert "kumotaan" in witness["quote"]
    assert "21―28, 28 a ja 40 §" in witness["quote"]
    assert detail["operation_source_xml_span_status"] == "available"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2023_71_chapter_insert_recovery_warning_is_visible(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("2023/71").resolve(
        jurisdiction="fi",
        provision="section:25c",
        as_of="2026-07-01",
    )

    assert payload["provision_status"] == "selected"
    assert payload["source"]["statute_id"] == "2025/1373"
    diagnostics = payload.get("diagnostics")
    assert isinstance(diagnostics, list)
    codes = [
        item["code"]
        for item in diagnostics
        if isinstance(item, dict)
    ]
    assert set(codes) >= {"COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"}
    assert codes.count("COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED") == 1
    for item in diagnostics:
        assert item["source_statute"] == "2025/1373"
        assert item["seam_blocking"] is False
        assert item["hash_role"] == "excluded_from_derived_state_hash"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2014_1429_broken_timeline_is_surfaced_not_clean() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test
    from lawvm.tools.timeline_integrity import (
        break_governs_as_of,
        sorted_breaks,
    )

    as_of = "2026-06-11"
    replay_meta: dict = {}
    master = replay_xml_for_test("2014/1429", quiet=True, replay_meta_out=replay_meta)
    breaks = sorted_breaks(
        attach_effective_dates(
            timeline_breaks_from_findings(master.findings),
            replay_meta.get("lineage") or (),
        )
    )
    governing_statute_breaks = tuple(
        item
        for item in breaks
        if item.scope == "statute" and break_governs_as_of(item, as_of)
    )

    payload = build_provision_state_response(
        timelines=master.timelines,
        statute_id="2014/1429",
        jurisdiction="fi",
        provision="section:30",
        as_of=as_of,
        title=master.title,
        timeline_breaks=breaks,
    )

    if governing_statute_breaks:
        # Broken timeline: the answer must be typed-unprovable, never a clean
        # legal fact. (Live as of 2026-06-11: break at 2025/1382.)
        assert payload["provision_status"] == "timeline_unverified"
        assert payload["timeline_broken_at"]["amendment_id"] == (
            governing_statute_breaks[0].amendment_id
        )
        assert payload["timeline_integrity"]["blocking"] is True
        assert payload["version"] is None
    else:
        # Replay break fixed upstream: the seam must serve cleanly again.
        assert payload["provision_status"] in ("selected", "absent")
        assert "timeline_broken_at" not in payload or (
            payload["timeline_integrity"]["blocking"] is False
        )


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_2014_938_section_51_failed_apply_is_governed_by_snapshot() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    master = replay_xml_for_test("2014/938", quiet=True)
    governed = [
        finding
        for finding in master.findings
        if finding.kind == "APPLY.FAILED_OPERATION_GOVERNED_BY_TIMELINE_SNAPSHOT"
        and finding.detail.get("target_chapter") == "8"
        and finding.detail.get("target_section") == "51"
    ]

    assert not [
        finding
        for finding in master.findings
        if finding.kind == "APPLY.FAILED_OPERATION"
        and finding.detail.get("target_chapter") == "8"
        and finding.detail.get("target_section") == "51"
    ]
    assert not [
        item
        for item in timeline_breaks_from_findings(master.findings)
        if item.diagnostic_code == "APPLY.FAILED_OPERATION"
        and item.target_chapter == "8"
        and item.target_section == "51"
    ]
    if governed:
        assert all(
            finding.detail.get("target_chapter") == "8"
            and finding.detail.get("target_section") == "51"
            for finding in governed
        )

    payload = resolve_provision_state(
        statute_id="2014/938",
        jurisdiction="fi",
        provision="section:51",
        as_of="2026-06-11",
        query_type="in_force",
    )

    assert payload["provision_status"] == "selected"
    assert "timeline_integrity" not in payload
    assert payload["source"]["statute_id"] == "2024/910"
    assert "vuokraindeksi" not in payload["text"]["rendered"]


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1992_1535_item_insert_failures_are_governed_by_parent_snapshot() -> None:
    from lawvm.finland.ops import FailedOp
    from tests.corpus_pin_helpers import replay_xml_for_test

    failed_ops: list[FailedOp] = []
    master = replay_xml_for_test(
        "1992/1535",
        mode="official_consolidation",
        quiet=True,
        failed_ops_out=failed_ops,
    )

    assert not [
        failed
        for failed in failed_ops
        if failed.amendment_id in {"1996/431", "2004/1288"}
        and failed.target_section == "76"
        and "76 § 1 mom" in failed.description
    ]

    governed = [
        finding
        for finding in master.findings
        if finding.kind == "APPLY.FAILED_OPERATION_GOVERNED_BY_PARENT_SNAPSHOT"
        and finding.detail.get("target_section") == "76"
        and finding.detail.get("target_subsection") == "1"
    ]
    descriptions = {finding.detail.get("failed_description") for finding in governed}

    legacy_descriptions = {
        "INSERT 76 § 1 mom 4a kohta",
        "INSERT 76 § 1 mom 4b kohta",
        "INSERT 76 § 1 mom 3a kohta",
    }
    if descriptions & legacy_descriptions:
        assert legacy_descriptions <= descriptions
        assert all(
            finding.detail.get("governance_basis")
            == "same_source_subsection_snapshot_payload_contains_item"
            for finding in governed
        )
        assert all(
            finding.detail.get("snapshot_op_id") == "snapshot_subsection_1_from_section_76"
            for finding in governed
        )


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_1982_182_failed_container_noops_do_not_claim_tree_touches() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    replay_meta: dict = {}
    master = replay_xml_for_test(
        "1982/182",
        mode="official_consolidation",
        quiet=True,
        replay_meta_out=replay_meta,
    )

    assert not [
        finding
        for finding in master.findings
        if finding.kind == "REPLAY_FAILED_OP_MUTATED_TREE"
    ]
    assert not [
        event
        for event in replay_meta.get("apply_mutation_events", [])
        if event.get("helper") == "_apply_container_op"
        and event.get("outcome") == "failed"
        and (
            event.get("resolved_target_path")
            or event.get("created_paths")
            or event.get("removed_paths")
            or event.get("replaced_paths")
            or event.get("placeholder_created_paths")
            or event.get("placeholder_consumed_paths")
        )
    ]


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_1992_1535_item_replacement_does_not_mutate_section_heading() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    def path_ends_with_heading(path: object) -> bool:
        if not isinstance(path, (list, tuple)) or not path:
            return False
        tail = path[-1]
        return isinstance(tail, (list, tuple)) and bool(tail) and tail[0] == "heading"

    replay_meta: dict = {}
    master = replay_xml_for_test(
        "1992/1535",
        mode="official_consolidation",
        quiet=True,
        replay_meta_out=replay_meta,
    )

    assert not [
        finding
        for finding in master.findings
        if finding.kind == "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"
        and finding.source_statute == "2001/196"
        and finding.detail.get("helper") == "_apply_deterministic_subsection_op"
        and any(path_ends_with_heading(path) for path in finding.detail.get("out_of_scope_paths", ()))
    ]
    assert not [
        event
        for event in replay_meta.get("apply_mutation_events", [])
        if event.get("source_statute") == "2001/196"
        and event.get("helper") == "_apply_deterministic_subsection_op"
        and (
            any(path_ends_with_heading(path) for path in event.get("created_paths", ()))
            or any(path_ends_with_heading(path) for path in event.get("replaced_paths", ()))
        )
    ]


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_1997_1412_section_11_drops_expired_temporary_render_tails(
    live_provision_state_runtime_for_statute,
) -> None:
    payload = live_provision_state_runtime_for_statute("1997/1412").resolve(
        jurisdiction="fi",
        provision="section:11",
        as_of="2026-06-11",
        query_type="in_force",
    )

    rendered = payload["text"]["rendered"]
    assert payload["provision_status"] == "selected"
    assert "20:tä prosenttia" not in rendered
    assert "lapsilisälain" not in rendered
    assert "epidemiakorvauksesta" not in rendered
    assert "alle 18-vuotiaan tulonsaajan ansiotuloista" in rendered


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
@pytest.mark.slow
def test_specimen_2002_1290_repealed_section_insert_occupancy_is_governed_by_snapshot() -> None:
    from tests.corpus_pin_helpers import replay_xml_for_test

    master = replay_xml_for_test("2002/1290", quiet=True)
    governed = [
        finding
        for finding in master.findings
        if finding.kind == "APPLY.OCCUPANCY_POLICY_GOVERNED_BY_TIMELINE_SNAPSHOT"
        and finding.source_statute == "2011/509"
        and finding.detail.get("target_chapter") == "4"
        and finding.detail.get("target_section") == "6"
    ]

    assert governed
    assert not [
        finding
        for finding in master.findings
        if finding.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"
        and finding.source_statute == "2011/509"
        and finding.detail.get("target_label") == "6"
    ]
    assert not [
        item
        for item in timeline_breaks_from_findings(master.findings)
        if item.diagnostic_code == "APPLY.OCCUPANCY_POLICY_VIOLATION"
        and item.amendment_id == "2011/509"
        and item.target_section == "6"
    ]
    assert [
        item
        for item in timeline_breaks_from_findings(master.findings)
        if item.diagnostic_code == "APPLY.OCCUPANCY_POLICY_VIOLATION"
        and item.amendment_id == "2021/861"
        and item.target_chapter == "11"
        and item.target_section == "4c"
    ]

    payload = resolve_provision_state(
        statute_id="2002/1290",
        jurisdiction="fi",
        provision="chapter:6/section:6",
        as_of="2026-06-11",
        query_type="in_force",
    )

    assert payload["provision_status"] in ("selected", "absent")
    assert "timeline_integrity" not in payload
