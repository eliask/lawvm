from __future__ import annotations

from argparse import Namespace
import hashlib
import json
import subprocess

import pytest

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row, validate_corpus_operation_evidence_row
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.open_law.audit import (
    audit_open_law_snapshot,
    execute_open_law_expiry,
    failed_codification_findings,
    replay_open_law_ops,
    resolve_open_law_path,
)
from lawvm.open_law.corpus_audit import (
    OpenLawOperationAuditRow,
    _finding_evidence_row,
    audit_maryland_corpus,
    audit_maryland_transition,
)
from lawvm.open_law.evidence_pack import _shareable_git_remote_url, write_maryland_evidence_pack
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.local_git import make_maryland_repos
from lawvm.open_law.models import OpenLawAction, OpenLawAnnotationLane, OpenLawFinding
from lawvm.open_law.planner import plan_maryland_comar_operation
from lawvm.open_law.xml import parse_open_law_xml, wrap_open_law_body_with_prefix
from lawvm.tools.open_law import _print_explain, _print_verify_pack


_BASE_XML = """<?xml version='1.0' encoding='utf-8'?>
<document xmlns="https://open.law/schemas/library" id="Code of Maryland Regulations">
  <heading>Code of Maryland Regulations</heading>
  <container>
    <prefix>Title</prefix>
    <num>10</num>
    <heading>Maryland Department of Health</heading>
    <container>
      <prefix>Subtitle</prefix>
      <num>41</num>
      <heading>Board of Examiners</heading>
      <container>
        <prefix>Chapter</prefix>
        <num>02</num>
        <heading>Code of Ethics</heading>
        <section>
          <prefix>Regulation</prefix>
          <num>.04</num>
          <heading>Special Responsibilities.</heading>
          <para>
            <num>A.</num>
            <text>Old text.</text>
          </para>
        </section>
      </container>
    </container>
  </container>
</document>
"""


_REPLACE_XML = """<?xml version='1.0' encoding='utf-8'?>
<document xmlns="https://open.law/schemas/library"
    xmlns:codify="https://open.law/schemas/codify"
    id="Editor Action 2026-01-22">
  <meta>
    <effective>2026-01-22</effective>
  </meta>
  <codify:replace history="false" doc="Code of Maryland Regulations" path="10|41|02|.04">
    <section>
      <prefix>Regulation</prefix>
      <num>.04</num>
      <heading>Special Responsibilities.</heading>
      <para>
        <num>A.</num>
        <text>New text.</text>
      </para>
    </section>
  </codify:replace>
</document>
"""


def test_parse_open_law_xml_preserves_direct_path_labels() -> None:
    tree = parse_open_law_xml(_BASE_XML)

    resolved = resolve_open_law_path(tree, ("10", "41", "02", ".04"))

    assert resolved.path_status == "resolved"
    assert resolved.tree_path == (
        ("hcontainer", "10"),
        ("hcontainer", "41"),
        ("hcontainer", "02"),
        ("section", ".04"),
    )


def test_parse_codify_replace_operation() -> None:
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    assert len(ops) == 1
    op = ops[0]
    assert op.action is OpenLawAction.REPLACE
    assert op.doc == "Code of Maryland Regulations"
    assert op.path == ("10", "41", "02", ".04")
    assert op.effective == "2026-01-22"
    assert op.history is False
    assert op.payload is not None
    assert op.payload.kind is IRNodeKind.SECTION
    assert op.payload.label == ".04"


def test_parse_codify_multiple_payload_children_records_blocking_diagnostic() -> None:
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:replace doc="Code of Maryland Regulations" path="10|41|02|.04">
            <heading>First payload</heading>
            <section><num>.04</num><text>Second payload.</text></section>
          </codify:replace>
        </document>
        """,
        source_id="editorial-actions/multiple-payload.xml",
    )

    op = ops[0]
    assert op.payload is None
    assert [finding.kind for finding in op.diagnostics] == ["open_law_codify_multiple_payload_children"]
    assert op.diagnostics[0].blocking is True


def test_parse_codify_unsupported_payload_child_records_blocking_diagnostic() -> None:
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:replace doc="Code of Maryland Regulations" path="10|41|02|.04">
            <section><num>.04</num><text>Supported payload.</text></section>
            <table><row><cell>Unsupported sibling.</cell></row></table>
          </codify:replace>
        </document>
        """,
        source_id="editorial-actions/unsupported-payload-child.xml",
    )

    op = ops[0]
    assert op.payload is None
    assert [finding.kind for finding in op.diagnostics] == ["open_law_codify_unsupported_payload_child"]
    assert op.diagnostics[0].blocking is True
    assert "table" in op.diagnostics[0].message


def test_parse_codify_expire_preserves_expire_date() -> None:
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
        </document>
        """,
        source_id="editorial-actions/expire.xml",
    )

    assert ops[0].action is OpenLawAction.EXPIRE
    assert ops[0].expire_date == "2026-11-20"


def test_replay_codify_replace_changes_exact_declared_target() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert not result.findings
    assert len(result.mutations) == 1
    assert result.mutations[0].open_law_path == ("10", "41", "02", ".04")
    section = result.tree.children[1].children[2].children[2].children[2]
    assert section.kind is IRNodeKind.SECTION
    assert "New text." in irnode_to_text(section)
    assert "Old text." not in irnode_to_text(section)


def test_replay_codify_replace_rejects_payload_target_identity_mismatch() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("<num>.04</num>", "<num>.05</num>", 1)
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/payload-mismatch.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_payload_target_mismatch"]
    assert result.findings[0].blocking is True
    assert "expected section:'.04', got section:'.05'" in result.findings[0].message


def test_replay_missing_target_emits_blocking_finding_without_mutation() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("10|41|02|.04", "10|41|99|.04")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_target_missing"]
    assert result.findings[0].blocking is True


def test_replay_multiple_payload_children_diagnostic_blocks_mutation() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    ops = parse_open_law_codify_ops(
        _REPLACE_XML.replace(
            "</section>",
            "</section><section><num>.05</num><text>Unclaimed sibling.</text></section>",
            1,
        ),
        source_id="editorial-actions/multiple-payload.xml",
    )

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_codify_multiple_payload_children"]
    assert result.findings[0].blocking is True


def test_replay_unsupported_payload_child_diagnostic_blocks_mutation() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    ops = parse_open_law_codify_ops(
        _REPLACE_XML.replace(
            "</section>",
            "</section><table><row><cell>Unsupported sibling.</cell></row></table>",
            1,
        ),
        source_id="editorial-actions/unsupported-payload-child.xml",
    )

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_codify_unsupported_payload_child"]
    assert result.findings[0].blocking is True


def test_expire_replays_as_tombstone_without_body_mutation() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = (
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
        </document>
        """
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/expire.xml")

    result = replay_open_law_ops(tree, ops)

    # Expiry is a jurisdiction tombstone, not a deletion of unrelated tree state.
    assert result.tree == tree
    assert not result.mutations
    assert ops[0].action is OpenLawAction.EXPIRE
    assert [finding.kind for finding in result.findings] == ["open_law_expire_tombstoned"]
    assert result.findings[0].blocking is False
    assert len(result.tombstones) == 1
    tombstone = result.tombstones[0]
    assert tombstone.open_law_path == ("regulations", "emergency", "25-138-E")
    assert tombstone.expire_date == "2026-11-20"
    assert tombstone.jurisdiction == "maryland_register"


def test_replace_or_insert_inserts_missing_target_with_visible_finding() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:replace-or-insert").replace(
        "</codify:replace>", "</codify:replace-or-insert>"
    ).replace(
        "<num>.04</num>", "<num>.05</num>"
    ).replace(
        "path=\"10|41|02|.04\"", "path=\"10|41|02|.05\""
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert ops[0].action is OpenLawAction.REPLACE_OR_INSERT
    assert [finding.kind for finding in result.findings] == ["open_law_replace_or_insert_inserted_missing_target"]
    assert len(result.mutations) == 1
    assert result.mutations[0].tree_path[-1] == ("section", ".05")


def test_replace_or_insert_rejects_missing_target_payload_label_mismatch() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:replace-or-insert").replace(
        "</codify:replace>", "</codify:replace-or-insert>"
    ).replace(
        "path=\"10|41|02|.04\"", "path=\"10|41|02|.06\""
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/payload-mismatch.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_payload_target_mismatch"]
    assert result.findings[0].blocking is True
    assert "expected label '.06', got section:'.04'" in result.findings[0].message


def test_expire_tombstone_is_non_blocking_even_in_strict_mode() -> None:
    # A replayed lifecycle result is a proven replay, not an unproven recovery;
    # strict mode does not block it (unlike an unknown/unhandled action).
    tree = parse_open_law_xml(_BASE_XML)
    xml = (
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
        </document>
        """
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/expire.xml")

    result = replay_open_law_ops(tree, ops, strict=True)

    assert ops[0].action is OpenLawAction.EXPIRE
    assert [finding.kind for finding in result.findings] == ["open_law_expire_tombstoned"]
    assert result.findings[0].blocking is False
    assert len(result.tombstones) == 1


def test_open_law_finding_evidence_row_uses_shared_disposition_envelope() -> None:
    row = OpenLawOperationAuditRow(
        before_branch="publication/2026-01-01.2026-01-01",
        after_branch="publication/2026-01-02.2026-01-02",
        action_path="editorial-actions/2026-01-02.xml",
        op_id="op-1",
        action="replace",
        codify_path=("10", "41", "02", ".04"),
        xml_path="10/41/02.xml",
        audit_status="diverged",
    )
    blocking_finding = OpenLawFinding(
        kind="open_law_publication_snapshot_mismatch",
        message="Snapshot mismatch.",
        op_id="op-1",
        path=("10", "41", "02", ".04"),
        blocking=True,
    )
    nonblocking_finding = OpenLawFinding(
        kind="open_law_snapshot_typography_projection",
        message="Typography projection.",
        op_id="op-1",
        path=("10", "41", "02", ".04"),
        blocking=False,
    )

    blocking_row = _finding_evidence_row(row, blocking_finding).to_dict()
    nonblocking_row = _finding_evidence_row(row, nonblocking_finding).to_dict()

    assert blocking_row["rule_id"] == "open_law_publication_snapshot_mismatch"
    assert blocking_row["family"] == "open_law_publication_snapshot_mismatch"
    assert blocking_row["phase"] == "audit"
    assert blocking_row["blocking"] is True
    assert blocking_row["strict_disposition"] == "block"
    assert blocking_row["quirks_disposition"] == "record"
    assert nonblocking_row["blocking"] is False
    assert nonblocking_row["strict_disposition"] == "record"
    assert validate_corpus_finding_evidence_row(blocking_row) == ()
    assert validate_corpus_finding_evidence_row(nonblocking_row) == ()


def test_snapshot_audit_accepts_publication_that_matches_declared_replace() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(_BASE_XML.replace("Old text.", "New text."))
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops)

    assert result.snapshot_matches_replay is True
    assert result.unexplained_paths == ()
    assert not result.findings


def test_snapshot_audit_ignores_annotations_as_text_state_compare_projection() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(
        _BASE_XML.replace("Old text.", "New text.").replace(
            "</section>",
            '<annotations><annotation type="History" display="false"/></annotations></section>',
        )
    )
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops, annotation_lane=OpenLawAnnotationLane.PUBLICATION_METADATA)

    assert result.snapshot_matches_replay is True
    assert result.unexplained_paths == ()
    assert [finding.kind for finding in result.findings] == ["open_law_snapshot_annotation_projection"]


def test_snapshot_audit_names_typography_projection_without_claiming_legal_mutation() -> None:
    before = parse_open_law_xml(_BASE_XML.replace("Old text.", '"Old" text.'))
    after = parse_open_law_xml(_BASE_XML.replace("Old text.", "“Old” text."))
    ops = ()

    result = audit_open_law_snapshot(before, after, ops)

    assert result.snapshot_matches_replay is True
    assert result.unexplained_paths == ()
    assert [finding.kind for finding in result.findings] == ["open_law_snapshot_typography_projection"]


def test_snapshot_audit_flags_publication_change_outside_declared_target() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(_BASE_XML.replace("Maryland Department of Health", "Changed Title"))
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops)

    assert result.snapshot_matches_replay is False
    assert result.unexplained_paths == ((("hcontainer", "10"), ("heading", "")),)
    assert [finding.kind for finding in result.findings] == [
        "open_law_publication_snapshot_mismatch",
        "open_law_unexplained_publication_mutation",
    ]


def test_explicit_path_prefix_wraps_partial_subtree_without_guessing() -> None:
    partial = parse_open_law_xml(
        """
        <container xmlns="https://open.law/schemas/library">
          <prefix>Chapter</prefix>
          <num>02</num>
          <section><num>.04</num><text>Chapter-only file.</text></section>
        </container>
        """
    )

    wrapped = wrap_open_law_body_with_prefix(partial, ("10", "41"))
    resolved = resolve_open_law_path(wrapped, ("10", "41", "02", ".04"))

    assert resolved.path_status == "resolved"
    assert resolved.tree_path == (
        ("hcontainer", "10"),
        ("hcontainer", "41"),
        ("hcontainer", "02"),
        ("section", ".04"),
    )


def test_planner_maps_heading_and_annotation_targets() -> None:
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:replace doc="Code of Maryland Regulations" path="10|21|heading"><heading>Subtitle</heading></codify:replace>
          <codify:replace doc="Code of Maryland Regulations" path="10|27|02|annos"><annotations/></codify:replace>
        </document>
        """,
        source_id="test.xml",
    )

    subtitle_heading = plan_maryland_comar_operation(ops[0])
    chapter_annos = plan_maryland_comar_operation(ops[1])

    assert subtitle_heading.xml_path == "us/md/exec/comar/10/21/index.xml"
    assert subtitle_heading.path_prefix == ("10",)
    assert chapter_annos.xml_path == "us/md/exec/comar/10/27/02.xml"
    assert chapter_annos.path_prefix == ("10", "27")


def test_heading_and_annotations_resolve_as_explicit_path_segments() -> None:
    tree = parse_open_law_xml(
        """
        <container xmlns="https://open.law/schemas/library">
          <num>02</num>
          <heading>Old heading</heading>
          <annotations><annotation type="History">History note</annotation></annotations>
        </container>
        """
    )

    wrapped = wrap_open_law_body_with_prefix(tree, ("10", "27"))

    assert resolve_open_law_path(wrapped, ("10", "27", "02", "heading")).path_status == "resolved"
    assert resolve_open_law_path(wrapped, ("10", "27", "02", "annos")).path_status == "resolved"


def test_corpus_transition_uses_only_new_after_branch_actions(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/after", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_transition("publication/before", "publication/after", repos=repos)

    assert report.summary["operation_rows"] == 1
    assert report.summary["matched"] == 1
    assert report.operation_rows[0].action_path == "editorial-actions/new.xml"


def test_corpus_audit_uses_suffixed_snapshots_over_rolling_publication_refs(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-01", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "rolling")
    _git_branch(codified_repo, "publication/2026-01-01")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", ("editorial-actions/old.xml",)))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before snapshot")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-01")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-02", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after snapshot")
    _git_branch(codified_repo, "publication/2026-01-02.2026-01-02")

    report = audit_maryland_corpus(repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["operation_rows"] == 1
    assert report.summary["matched"] == 1
    assert report.operation_rows[0].before_branch == "publication/2026-01-01.2026-01-01"
    assert report.operation_rows[0].after_branch == "publication/2026-01-02.2026-01-02"


def test_corpus_audit_replays_annotation_metadata_targets_without_body_claim(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|annos">
        <annotations><annotation type="History">New history.</annotation></annotations>
      </codify:replace>
    </document>
    """
    _write(source_repo / "editorial-actions" / "annos.xml", action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace("</container>", "<annotations><annotation type=\"History\">Old history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/annos.xml",)))
    _write(codified_repo / "editorial-actions" / "annos.xml", action)
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace("</container>", "<annotations><annotation type=\"History\">New history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "after body")
    _git_branch(codified_repo, "publication/after-with-body")

    report = audit_maryland_transition("publication/before", "publication/after-with-body", repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["metadata_matched"] == 1
    assert report.operation_rows[0].audit_status == "metadata_matched"
    assert [finding.kind for finding in report.operation_rows[0].findings] == ["open_law_metadata_target_replayed"]


def test_corpus_audit_flags_annotation_operation_with_body_mutation(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|annos">
        <annotations><annotation type="History">New history.</annotation></annotations>
      </codify:replace>
    </document>
    """
    _write(source_repo / "editorial-actions" / "annos.xml", action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace("</container>", "<annotations><annotation type=\"History\">Old history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/annos.xml",)))
    _write(codified_repo / "editorial-actions" / "annos.xml", action)
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Changed body text.").replace("</container>", "<annotations><annotation type=\"History\">New history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition("publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["metadata_diverged"] == 1
    assert report.operation_rows[0].unexplained_path_count == 1
    assert [finding.kind for finding in report.operation_rows[0].findings] == ["open_law_metadata_unexplained_body_mutation"]


def test_corpus_audit_projects_only_generated_hidden_history_metadata(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|annos">
        <annotations><annotation type="History">New history.</annotation></annotations>
      </codify:replace>
    </document>
    """
    _write(source_repo / "editorial-actions" / "annos.xml", action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace("</container>", "<annotations><annotation type=\"History\">Old history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    generated_history = (
        '<annotation type="History" display="false" doc="Maryland Register, Volume 53, Issue 1" '
        'path="regulations|final|25-242-F" eff="2026-01-19"/>'
    )
    generated_editor_history = '<annotation type="History" display="false" doc="Editor Action 2026-03-09" path="" eff="2026-03-09"/>'
    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/annos.xml",)))
    _write(codified_repo / "editorial-actions" / "annos.xml", action)
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace(
            "</container>",
            f"<annotations><annotation type=\"History\">New history.</annotation>{generated_history}{generated_editor_history}</annotations></container>",
        ),
    )
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition("publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["metadata_matched"] == 1
    assert [finding.kind for finding in report.operation_rows[0].findings] == [
        "open_law_metadata_generated_history_projection",
        "open_law_metadata_target_replayed",
    ]


def test_corpus_audit_does_not_project_generic_display_false_metadata(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|annos">
        <annotations><annotation type="History">New history.</annotation></annotations>
      </codify:replace>
    </document>
    """
    _write(source_repo / "editorial-actions" / "annos.xml", action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace("</container>", "<annotations><annotation type=\"History\">Old history.</annotation></annotations></container>"),
    )
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/annos.xml",)))
    _write(codified_repo / "editorial-actions" / "annos.xml", action)
    _write(
        codified_repo / "us/md/exec/comar/10/41/02.xml",
        _chapter_xml("Old text.").replace(
            "</container>",
            (
                "<annotations><annotation type=\"History\">New history.</annotation>"
                "<annotation type=\"Authority\" display=\"false\">Hidden authority.</annotation></annotations></container>"
            ),
        ),
    )
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition("publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["metadata_diverged"] == 1
    assert [finding.kind for finding in report.operation_rows[0].findings] == ["open_law_metadata_snapshot_mismatch"]


def test_corpus_audit_replays_register_expire_as_lifecycle_tombstone(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
    </document>
    """
    _write(source_repo / "editorial-actions" / "expire.xml", action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/expire.xml",)))
    _write(codified_repo / "editorial-actions" / "expire.xml", action)
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition("publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo))

    assert report.summary["lifecycle_tombstoned"] == 1
    assert report.summary["lifecycle_unsupported"] == 0
    assert report.operation_rows[0].audit_status == "lifecycle_tombstoned"
    assert report.operation_rows[0].expire_date == "2026-11-20"
    assert [finding.kind for finding in report.operation_rows[0].findings] == ["open_law_expire_tombstoned"]


def test_evidence_pack_writes_summary_and_machine_reports(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-01")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-02", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/2026-01-02.2026-01-02")

    pack = write_maryland_evidence_pack(tmp_path / "pack", repos=make_maryland_repos(source_repo, codified_repo))

    assert pack.report.summary["matched"] == 1
    assert pack.manifest_path == tmp_path / "pack" / "manifest.json"
    assert pack.summary_json_path == tmp_path / "pack" / "summary.json"
    assert pack.operation_audits_path == tmp_path / "pack" / "operation_audits.jsonl"
    assert pack.findings_path == tmp_path / "pack" / "findings.jsonl"
    assert (tmp_path / "pack" / "manifest.json").exists()
    assert (tmp_path / "pack" / "evidence_pack_manifest.json").exists()
    assert (tmp_path / "pack" / "operation_audits.jsonl").exists()
    assert (tmp_path / "pack" / "findings.jsonl").exists()
    summary_text = pack.summary_path.read_text(encoding="utf-8")
    assert "## What LawVM Claims" in summary_text
    assert "- source clone HEAD:" in summary_text
    assert "- codified clone HEAD:" in summary_text
    assert "- LawVM generator commit:" in summary_text
    assert "- LawVM generator dirty:" in summary_text
    assert '"clean_replace"' in pack.exemplars_path.read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "pack" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["local_repositories"]["source"]["label"] == "maryland-dsd/law-xml"
    assert len(manifest["local_repositories"]["source"]["head_commit"]) == 40
    assert manifest["local_repositories"]["source"]["current_branch"] == "main"
    assert manifest["local_repositories"]["source"]["remotes"] == []
    assert manifest["local_repositories"]["codified"]["label"] == "maryland-dsd/law-xml-codified"
    assert len(manifest["local_repositories"]["codified"]["head_commit"]) == 40
    artifact_manifest = json.loads(pack.artifact_manifest_path.read_text(encoding="utf-8"))
    assert artifact_manifest["generator"]["tool"] == "lawvm open-law evidence-pack"
    assert isinstance(artifact_manifest["generator"]["repository"], str)
    assert "/" + "home" + "/" not in artifact_manifest["generator"]["repository"]
    assert "/" + "Users" + "/" not in artifact_manifest["generator"]["repository"]
    assert isinstance(artifact_manifest["generator"]["git_commit"], str)
    assert artifact_manifest["generator"]["git_dirty"] in {True, False, None}
    artifact_paths = {item["path"] for item in artifact_manifest["files"]}
    assert artifact_paths == {
        "manifest.json",
        "summary.json",
        "operation_audits.jsonl",
        "findings.jsonl",
        "exemplars.json",
        "summary.md",
    }
    assert all(len(item["sha256"]) == 64 for item in artifact_manifest["files"])
    operation_audits_entry = next(item for item in artifact_manifest["files"] if item["path"] == "operation_audits.jsonl")
    operation_audits_bytes = (tmp_path / "pack" / "operation_audits.jsonl").read_bytes()
    assert operation_audits_entry["sha256"] == hashlib.sha256(operation_audits_bytes).hexdigest()
    operation_rows = [
        json.loads(line)
        for line in (tmp_path / "pack" / "operation_audits.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    finding_rows = [
        json.loads(line)
        for line in (tmp_path / "pack" / "findings.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert all(validate_corpus_operation_evidence_row(row["evidence_row"]) == () for row in operation_rows)
    assert all(validate_corpus_finding_evidence_row(row["evidence_row"]) == () for row in finding_rows)


def test_open_law_generator_remote_normalizes_github_ssh_url() -> None:
    assert _shareable_git_remote_url("git@github.com:eliask/lawvm.git") == "https://github.com/eliask/lawvm.git"
    assert _shareable_git_remote_url("https://github.com/eliask/lawvm.git") == "https://github.com/eliask/lawvm.git"


def test_open_law_verify_pack_checks_artifacts_and_evidence_rows(tmp_path, capsys) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-01")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-02", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/2026-01-02.2026-01-02")

    write_maryland_evidence_pack(tmp_path / "pack", repos=make_maryland_repos(source_repo, codified_repo))

    _print_verify_pack(Namespace(report_dir=str(tmp_path / "pack"), require_clean_generator=False, json=False))

    out = capsys.readouterr().out
    assert "files=6 operation_rows=1 finding_rows=0 generator_clean=" in out
    assert "issues=0" in out


def test_open_law_verify_pack_fails_on_checksum_mismatch(tmp_path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "manifest.json").write_text("original\n", encoding="utf-8")
    (pack_dir / "summary.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "operation_audits.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "findings.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "exemplars.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    files = []
    for name in ("manifest.json", "summary.json", "operation_audits.jsonl", "findings.jsonl", "exemplars.json", "summary.md"):
        data = (pack_dir / name).read_bytes()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (pack_dir / "evidence_pack_manifest.json").write_text(json.dumps({"files": files}) + "\n", encoding="utf-8")
    (pack_dir / "manifest.json").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=False, json=False))


def test_open_law_verify_pack_can_require_clean_generator(tmp_path, capsys) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    summary = {
        "operation_rows": 0,
        "matched": 0,
        "diverged": 0,
        "planning_failed": 0,
        "metadata_unsupported": 0,
        "metadata_matched": 0,
        "metadata_diverged": 0,
        "lifecycle_unsupported": 0,
        "lifecycle_tombstoned": 0,
        "snapshot_missing": 0,
        "findings": 0,
        "unexplained_paths": 0,
    }
    (pack_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    (pack_dir / "operation_audits.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "findings.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "exemplars.json").write_text("{}\n", encoding="utf-8")
    generator = {
        "tool": "lawvm open-law evidence-pack",
        "repository": "/repo",
        "git_commit": "a" * 40,
        "git_dirty": True,
    }
    (pack_dir / "summary.md").write_text(
        f"# Summary\n\n- LawVM generator commit: `{generator['git_commit']}`\n- LawVM generator repository: `{generator['repository']}`\n",
        encoding="utf-8",
    )
    files = []
    for name in ("manifest.json", "summary.json", "operation_audits.jsonl", "findings.jsonl", "exemplars.json", "summary.md"):
        data = (pack_dir / name).read_bytes()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    manifest_path = pack_dir / "evidence_pack_manifest.json"
    manifest_path.write_text(json.dumps({"generator": generator, "files": files}) + "\n", encoding="utf-8")

    _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=False, json=False))
    assert "issues=0" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=True, json=False))

    generator["git_dirty"] = False
    manifest_path.write_text(json.dumps({"generator": generator, "files": files}) + "\n", encoding="utf-8")
    _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=True, json=False))
    assert "generator_clean=True" in capsys.readouterr().out


def test_open_law_verify_pack_rejects_developer_local_generator_repository(tmp_path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    summary = {
        "operation_rows": 0,
        "matched": 0,
        "diverged": 0,
        "planning_failed": 0,
        "metadata_unsupported": 0,
        "metadata_matched": 0,
        "metadata_diverged": 0,
        "lifecycle_unsupported": 0,
        "lifecycle_tombstoned": 0,
        "snapshot_missing": 0,
        "findings": 0,
        "unexplained_paths": 0,
    }
    for name, text in {
        "manifest.json": "{}\n",
        "summary.json": json.dumps(summary) + "\n",
        "operation_audits.jsonl": "",
        "findings.jsonl": "",
        "exemplars.json": "{}\n",
        "summary.md": "# Summary\n",
    }.items():
        (pack_dir / name).write_text(text, encoding="utf-8")
    files = []
    for name in ("manifest.json", "summary.json", "operation_audits.jsonl", "findings.jsonl", "exemplars.json", "summary.md"):
        data = (pack_dir / name).read_bytes()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    generator = {
        "tool": "lawvm open-law evidence-pack",
        "repository": "/" + "home" + "/example/LawVM",
        "git_commit": "a" * 40,
        "git_dirty": False,
    }
    (pack_dir / "evidence_pack_manifest.json").write_text(
        json.dumps({"generator": generator, "files": files}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=True, json=False))


def test_open_law_verify_pack_requires_canonical_manifest_file_set(tmp_path) -> None:
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    summary = {
        "operation_rows": 0,
        "matched": 0,
        "diverged": 0,
        "planning_failed": 0,
        "metadata_unsupported": 0,
        "metadata_matched": 0,
        "metadata_diverged": 0,
        "lifecycle_unsupported": 0,
        "lifecycle_tombstoned": 0,
        "snapshot_missing": 0,
        "findings": 0,
        "unexplained_paths": 0,
    }
    for name, text in {
        "manifest.json": "{}\n",
        "summary.json": json.dumps(summary) + "\n",
        "operation_audits.jsonl": "",
        "findings.jsonl": "",
        "exemplars.json": "{}\n",
        "summary.md": "# Summary\n",
        "extra.txt": "extra\n",
    }.items():
        (pack_dir / name).write_text(text, encoding="utf-8")
    files = []
    for name in ("manifest.json", "summary.json", "operation_audits.jsonl", "findings.jsonl", "extra.txt"):
        data = (pack_dir / name).read_bytes()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    generator = {
        "tool": "lawvm open-law evidence-pack",
        "repository": "/repo",
        "git_commit": "a" * 40,
        "git_dirty": False,
    }
    (pack_dir / "evidence_pack_manifest.json").write_text(
        json.dumps({"generator": generator, "files": files}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(pack_dir), require_clean_generator=True, json=False))


def test_open_law_verify_pack_fails_on_stale_summary_even_when_checksum_matches(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-01")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-02", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/2026-01-02.2026-01-02")

    pack = write_maryland_evidence_pack(tmp_path / "pack", repos=make_maryland_repos(source_repo, codified_repo))
    summary = json.loads(pack.summary_json_path.read_text(encoding="utf-8"))
    summary["matched"] = 0
    pack.summary_json_path.write_text(json.dumps(summary) + "\n", encoding="utf-8")
    _refresh_pack_manifest_entry(pack.artifact_manifest_path, pack.summary_json_path)

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(tmp_path / "pack"), require_clean_generator=False, json=False))


def test_open_law_verify_pack_fails_on_summary_md_missing_provenance_even_when_checksum_matches(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-01")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/2026-01-02", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/2026-01-02.2026-01-02")

    pack = write_maryland_evidence_pack(tmp_path / "pack", repos=make_maryland_repos(source_repo, codified_repo))
    artifact_manifest = json.loads(pack.artifact_manifest_path.read_text(encoding="utf-8"))
    commit = artifact_manifest["generator"]["git_commit"]
    summary_text = pack.summary_path.read_text(encoding="utf-8")
    pack.summary_path.write_text(summary_text.replace(commit, "missing-generator-commit"), encoding="utf-8")
    _refresh_pack_manifest_entry(pack.artifact_manifest_path, pack.summary_path)

    with pytest.raises(SystemExit):
        _print_verify_pack(Namespace(report_dir=str(tmp_path / "pack"), require_clean_generator=False, json=False))


def test_open_law_explain_text_includes_evidence_dispositions(tmp_path, capsys) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    row = {
        "op_id": "editorial-actions/example.xml:1",
        "audit_status": "lifecycle_unsupported",
        "action": "expire",
        "codify_path": ["regulations", "emergency", "25-138-E"],
        "before_branch": "publication/before",
        "after_branch": "publication/after",
        "action_path": "editorial-actions/example.xml",
        "xml_path": "",
        "expire_date": "2026-11-20",
        "changed_path_count": 0,
        "unexplained_path_count": 0,
        "snapshot_matches_replay": False,
        "evidence_row": {
            "evidence_status": "unsupported",
            "canonical_family": "",
            "strict_disposition": "block",
            "quirks_disposition": "record_unsupported",
        },
        "findings": [],
    }
    (report_dir / "operation_audits.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    _print_explain(Namespace(report_dir=str(report_dir), op_id="", status="", limit=1, json=False))

    out = capsys.readouterr().out
    assert "evidence: status=unsupported canonical=- strict=block quirks=record_unsupported" in out


def _refresh_pack_manifest_entry(manifest_path, artifact_path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = artifact_path.read_bytes()
    for item in manifest["files"]:
        if item["path"] == artifact_path.name:
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")


def test_unknown_codify_action_is_distinct_finding_not_silent_skip() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:renumber").replace(
        "</codify:replace>", "</codify:renumber>"
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert ops[0].action is OpenLawAction.UNSUPPORTED
    assert ops[0].raw_action == "renumber"
    assert [finding.kind for finding in result.findings] == ["open_law_unknown_codify_action"]
    assert "stable operation language" in result.findings[0].message
    assert result.findings[0].blocking is False


def test_unknown_codify_action_blocks_in_strict_mode() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:renumber").replace(
        "</codify:replace>", "</codify:renumber>"
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops, strict=True)

    assert [finding.kind for finding in result.findings] == ["open_law_unknown_codify_action"]
    assert result.findings[0].blocking is True


def test_recognized_expire_action_replays_as_tombstone_not_unknown_action() -> None:
    # A recognized codify verb (expire) is replayed on its own lifecycle lane; it
    # must never fall through to the unknown-action finding reserved for verbs
    # outside the stable vocabulary.
    tree = parse_open_law_xml(_BASE_XML)
    xml = (
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
        </document>
        """
    )
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/expire.xml")

    result = replay_open_law_ops(tree, ops)

    assert ops[0].action is OpenLawAction.EXPIRE
    assert [finding.kind for finding in result.findings] == ["open_law_expire_tombstoned"]
    assert "open_law_unknown_codify_action" not in [finding.kind for finding in result.findings]


def test_failed_codification_instruction_is_marked_source_pathology() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("10|41|02|.04", "10|41|99|.04")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_target_missing"]
    assert result.findings[0].source_pathology is True


def test_payload_target_mismatch_is_marked_source_pathology() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("<num>.04</num>", "<num>.05</num>", 1)
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/payload-mismatch.xml")

    result = replay_open_law_ops(tree, ops)

    assert [finding.kind for finding in result.findings] == ["open_law_payload_target_mismatch"]
    assert result.findings[0].source_pathology is True


def test_planning_failure_is_marked_source_pathology() -> None:
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:replace doc="Code of Maryland Regulations" path="10|41"><heading>Too short.</heading></codify:replace>
        </document>
        """,
        source_id="editorial-actions/short-path.xml",
    )

    plan = plan_maryland_comar_operation(ops[0])

    assert plan.plan_status == "failed"
    assert plan.finding is not None
    assert plan.finding.source_pathology is True


def test_annotation_lane_unset_compares_annotations_and_flags_policy() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(
        _BASE_XML.replace("Old text.", "New text.").replace(
            "</section>",
            '<annotations><annotation type="History" display="false"/></annotations></section>',
        )
    )
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops)

    # Annotations are not discarded; the unset policy is flagged and the added
    # annotation surfaces as an unexplained legal-text change.
    assert result.snapshot_matches_replay is False
    finding_kinds = [finding.kind for finding in result.findings]
    assert "open_law_annotation_lane_policy_unset" in finding_kinds
    assert "open_law_snapshot_annotation_projection" not in finding_kinds


def test_annotation_lane_official_code_compares_annotations_as_legal_text() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(_BASE_XML.replace("Old text.", "New text."))
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops, annotation_lane=OpenLawAnnotationLane.OFFICIAL_CODE)

    # No annotations present, so official-code mode is a clean match with no
    # projection finding and no policy-unset finding.
    assert result.snapshot_matches_replay is True
    assert not result.findings


def test_annotation_lane_publication_metadata_projects_annotations() -> None:
    before = parse_open_law_xml(_BASE_XML)
    after = parse_open_law_xml(
        _BASE_XML.replace("Old text.", "New text.").replace(
            "</section>",
            '<annotations><annotation type="History" display="false"/></annotations></section>',
        )
    )
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    result = audit_open_law_snapshot(before, after, ops, annotation_lane=OpenLawAnnotationLane.PUBLICATION_METADATA)

    assert result.snapshot_matches_replay is True
    assert [finding.kind for finding in result.findings] == ["open_law_snapshot_annotation_projection"]


def test_corpus_audit_flags_non_reproducible_publication_observationally(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(
        codified_repo / "index.xml",
        _index_xml(
            "publication/after",
            ("editorial-actions/old.xml", "editorial-actions/new.xml"),
            reproducible=False,
        ),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition(
        "publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo)
    )

    assert report.summary["operation_rows"] == 1
    # The body comparison still matches; the reproducibility gate is observational.
    assert report.summary["matched"] == 1
    row = report.operation_rows[0]
    assert row.publication_reproducible is False
    finding_kinds = [finding.kind for finding in row.findings]
    assert "open_law_publication_not_reproducible" in finding_kinds
    non_reproducible = next(f for f in row.findings if f.kind == "open_law_publication_not_reproducible")
    assert non_reproducible.blocking is False


def test_corpus_audit_reproducible_publication_has_no_gate_finding(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ("editorial-actions/old.xml",)))
    _write(codified_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Ignored old text."))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(
        codified_repo / "index.xml",
        _index_xml("publication/after", ("editorial-actions/old.xml", "editorial-actions/new.xml")),
    )
    _write(codified_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("New text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition(
        "publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo)
    )

    row = report.operation_rows[0]
    assert row.publication_reproducible is True
    assert "open_law_publication_not_reproducible" not in [finding.kind for finding in row.findings]


def test_corpus_body_lane_projects_annotations_when_action_mixes_body_and_metadata(tmp_path) -> None:
    # An action that carries both a body replace and a companion annos replace
    # in the same chapter. The body lane must keep projecting annotations out
    # (the annos op owns them in the metadata lane); the annotation change must
    # not surface as an unexplained body mutation.
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    action = """
    <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|.04">
        <section><prefix>Regulation</prefix><num>.04</num><heading>Special Responsibilities.</heading><para><num>A.</num><text>New text.</text></para></section>
      </codify:replace>
      <codify:replace doc="Code of Maryland Regulations" path="10|41|02|annos">
        <annotations><annotation type="History">New history.</annotation></annotations>
      </codify:replace>
    </document>
    """
    _write(source_repo / "editorial-actions" / "mixed.xml", action)
    _git_commit_all(source_repo, "source")

    before_chapter = _chapter_xml("Old text.").replace(
        "</container>", '<annotations><annotation type="History">Old history.</annotation></annotations></container>'
    )
    after_chapter = _chapter_xml("New text.").replace(
        "</container>", '<annotations><annotation type="History">New history.</annotation></annotations></container>'
    )
    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", before_chapter)
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/mixed.xml",)))
    _write(codified_repo / "editorial-actions" / "mixed.xml", action)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", after_chapter)
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition(
        "publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo)
    )

    body_row = next(row for row in report.operation_rows if row.codify_path[-1] == ".04")
    metadata_row = next(row for row in report.operation_rows if row.codify_path[-1] == "annos")
    assert body_row.audit_status == "matched"
    assert body_row.unexplained_path_count == 0
    assert "open_law_publication_snapshot_mismatch" not in [finding.kind for finding in body_row.findings]
    assert "open_law_unexplained_publication_mutation" not in [finding.kind for finding in body_row.findings]
    assert "open_law_annotation_lane_policy_unset" not in [finding.kind for finding in body_row.findings]
    assert metadata_row.audit_status == "metadata_matched"


def test_failed_codification_findings_umbrella_wraps_blocking_source_pathology() -> None:
    # Item 3: a failed codification instruction is a SOURCE bug, surfaced under a
    # dedicated named rule id, never a replay-side recovery.
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("10|41|02|.04", "10|41|99|.04")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")
    result = replay_open_law_ops(tree, ops)

    umbrella = failed_codification_findings(result.findings)

    assert [finding.kind for finding in result.findings] == ["open_law_target_missing"]
    assert [finding.kind for finding in umbrella] == ["open_law_failed_codification_source_bug"]
    assert umbrella[0].source_pathology is True
    assert umbrella[0].blocking is True
    assert "source bug" in umbrella[0].message
    # Idempotent: re-classifying does not stack umbrella findings.
    assert failed_codification_findings(result.findings + umbrella) == umbrella


def test_failed_codification_findings_ignores_non_pathology_findings() -> None:
    # A clean typography projection is not a failed codification and must not be
    # promoted into the source-bug lane.
    findings = (
        OpenLawFinding(
            kind="open_law_snapshot_typography_projection",
            message="typography.",
            blocking=False,
            source_pathology=False,
        ),
    )
    assert failed_codification_findings(findings) == ()


def test_corpus_audit_surfaces_failed_codification_source_bug_lane(tmp_path) -> None:
    # Item 3 at corpus level: a body op whose declared target is absent in the
    # source tree surfaces the named source-bug finding and never mutates.
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    missing_target_action = _REPLACE_XML.replace("10|41|02|.04", "10|41|02|.99")
    _write(source_repo / "editorial-actions" / "miss.xml", missing_target_action)
    _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/before", ()))
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "before")
    _git_branch(codified_repo, "publication/before")

    _write(codified_repo / "index.xml", _index_xml("publication/after", ("editorial-actions/miss.xml",)))
    _write(codified_repo / "editorial-actions" / "miss.xml", missing_target_action)
    _write(codified_repo / "us/md/exec/comar/10/41/02.xml", _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "after")
    _git_branch(codified_repo, "publication/after")

    report = audit_maryland_transition(
        "publication/before", "publication/after", repos=make_maryland_repos(source_repo, codified_repo)
    )

    row = report.operation_rows[0]
    finding_kinds = [finding.kind for finding in row.findings]
    assert "open_law_target_missing" in finding_kinds
    assert "open_law_failed_codification_source_bug" in finding_kinds
    source_bug = next(f for f in row.findings if f.kind == "open_law_failed_codification_source_bug")
    assert source_bug.source_pathology is True


def test_expire_tombstone_carrier_is_self_describing() -> None:
    # Item 5: the executed tombstone is a typed, owned lifecycle carrier.
    ops = parse_open_law_codify_ops(
        """
        <document xmlns="https://open.law/schemas/library" xmlns:codify="https://open.law/schemas/codify">
          <codify:expire doc="Maryland Register, Volume 52, Issue 26" path="regulations|emergency|25-138-E" date="2026-11-20"/>
        </document>
        """,
        source_id="editorial-actions/expire.xml",
    )

    tombstone, finding = execute_open_law_expiry(ops[0])

    assert tombstone.doc == "Maryland Register, Volume 52, Issue 26"
    assert tombstone.open_law_path == ("regulations", "emergency", "25-138-E")
    assert tombstone.expire_date == "2026-11-20"
    assert tombstone.jurisdiction == "maryland_register"
    assert finding.kind == "open_law_expire_tombstoned"
    assert finding.blocking is False


def test_compiled_snapshot_never_authorizes_source_replay(tmp_path) -> None:
    # Item 6 invariant: the compiled (after) tree is only a comparison surface;
    # it must never change what source replay produces. Replaying the same source
    # ops against the same source (before) tree yields identical mutations and
    # identical replay tree regardless of which compiled snapshot it is later
    # compared against.
    before = parse_open_law_xml(_BASE_XML)
    ops = parse_open_law_codify_ops(_REPLACE_XML, source_id="editorial-actions/2026-01-22.xml")

    faithful_after = parse_open_law_xml(_BASE_XML.replace("Old text.", "New text."))
    wrong_after = parse_open_law_xml(_BASE_XML.replace("Maryland Department of Health", "Tampered Title"))

    faithful = audit_open_law_snapshot(before, faithful_after, ops)
    tampered = audit_open_law_snapshot(before, wrong_after, ops)

    # Source-lane replay is a pure function of (source tree, declared ops); the
    # compiled snapshot only flips the comparison verdict, never the replay.
    assert faithful.replay.tree == tampered.replay.tree
    assert faithful.replay.mutations == tampered.replay.mutations
    assert faithful.snapshot_matches_replay is True
    assert tampered.snapshot_matches_replay is False


def _chapter_xml(text: str) -> str:
    return f"""
    <container xmlns="https://open.law/schemas/library">
      <prefix>Chapter</prefix>
      <num>02</num>
      <heading>Code of Ethics</heading>
      <section><prefix>Regulation</prefix><num>.04</num><heading>Special Responsibilities.</heading><para><num>A.</num><text>{text}</text></para></section>
    </container>
    """


def _index_xml(publication: str, action_paths: tuple[str, ...], *, reproducible: bool = True) -> str:
    includes = "\n".join(f'<xi:include href="./{path}"/>' for path in action_paths)
    reproducible_attr = "true" if reproducible else "false"
    return f"""
    <library xmlns="https://open.law/schemas/library" xmlns:xi="http://www.w3.org/2001/XInclude">
      <meta>
        <build>
          <repositories><repository name="maryland-dsd/law-xml" commit="abcdef1"/></repositories>
          <platform version="test" reproducible="{reproducible_attr}"/>
          <build-date>2026-01-01</build-date>
          <codified-date>2026-01-01</codified-date>
          <publication>{publication}</publication>
        </build>
      </meta>
      <collection name="editorial-actions" display="false">{includes}</collection>
    </library>
    """


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_init(path) -> None:
    path.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "checkout", "-q", "-b", "main"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.email", "test@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Test"), check=True)


def _git_commit_all(path, message: str) -> None:
    subprocess.run(("git", "-C", str(path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-q", "-m", message), check=True)


def _git_branch(path, branch: str) -> None:
    subprocess.run(("git", "-C", str(path), "branch", branch), check=True)


def test_shareable_git_remote_url_normalize_github_ssh_to_https() -> None:
    # AGENTS §2.9 guard-liveness on the local-path-remote-leak fix.
    # Regression: a developer-local checkout whose remote.origin.url is a
    # local path (a sibling-clone lookup, common for offline or dev-loop setups)
    # MUST NOT leak that local path into a serialised evidence-pack manifest.
    # The verify-pack leak guard catches the symptom;
    # _shareable_git_remote_url + _is_local_path_remote catch the leak at the
    # emission source, returning the repo's leaf directory name instead.
    # GitHub SSH remotes still normalize to HTTPS, and recognised shareable
    # scheme URIs (https/ssh/git / github.com host shapes) pass through.
    # file:// URIs are local-path remotes dressed as URIs — also fall back.
    # (Fixture paths avoid the developer-local-path markers the release-hygiene
    # gate itself watches for — they are still absolute Unix / Windows drive-
    # letter / file:// shapes the predicate must catch.)
    from lawvm.open_law.evidence_pack import (
        _is_local_path_remote,
        _shareable_git_remote_url,
    )

    # GitHub SSH -> HTTPS normalization preserved.
    assert _shareable_git_remote_url("git@github.com:owner/repo.git") == (
        "https://github.com/owner/repo.git"
    )
    # Recognised shareable URIs pass through verbatim.
    for shareable in (
        "https://github.com/owner/repo.git",
        "http://example.invalid/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git://example.invalid/repo.git",
    ):
        assert _shareable_git_remote_url(shareable) == shareable, shareable
    # Bare github.com host shapes pass through.
    assert _shareable_git_remote_url("github.com/owner/repo.git") == (
        "github.com/owner/repo.git"
    )

    # Local-path remotes (the leak): absolute Unix paths + Windows drive-letters
    # + ./ ../ relative refs + file:// URIs all fall back to the leaf name when
    # one is supplied, so the manifest never leaks the on-disk path.
    for local_path_remote in (
        "/srv/local/lawvm.nz",
        "/var/git/lawvm.nz",
        "./sibling/lawvm.nz",
        "../upstream/lawvm.nz",
        "C:\\dev\\lawvm.nz",
        "C:/dev/lawvm.nz",
        "file:///srv/local/lawvm.nz",
    ):
        assert _is_local_path_remote(local_path_remote) or local_path_remote.startswith("file://"), (
            f"local-path predicate missed: {local_path_remote}"
        )
        assert (
            _shareable_git_remote_url(local_path_remote, fallback_leaf="LawVM") == "LawVM"
        ), f"local-path remote leaked verbatim: {local_path_remote}"

    # No fallback_leaf supplied: the path is returned verbatim so the verify-pack
    # leak guard can flag the leak (never silent). Preserves the prior
    # behaviour for any non-library caller and surfaces the path as a typed
    # issue instead of suppressing it.
    leaked_path = "/srv/local/lawvm.nz"
    leaked = _shareable_git_remote_url(leaked_path)
    assert leaked == leaked_path
