from __future__ import annotations

from argparse import Namespace
import hashlib
import json
import subprocess

import pytest

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row, validate_corpus_operation_evidence_row
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.open_law.audit import audit_open_law_snapshot, replay_open_law_ops, resolve_open_law_path
from lawvm.open_law.corpus_audit import audit_maryland_corpus, audit_maryland_transition
from lawvm.open_law.evidence_pack import write_maryland_evidence_pack
from lawvm.open_law.codify import parse_open_law_codify_ops
from lawvm.open_law.local_git import make_maryland_repos
from lawvm.open_law.models import OpenLawAction
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

    assert resolved.status == "resolved"
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


def test_replay_missing_target_emits_blocking_finding_without_mutation() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("10|41|02|.04", "10|41|99|.04")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert [finding.kind for finding in result.findings] == ["open_law_target_missing"]
    assert result.findings[0].blocking is True


def test_unsupported_codify_action_is_visible_and_non_mutating_in_quirks_mode() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:expire").replace("</codify:replace>", "</codify:expire>")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops)

    assert result.tree == tree
    assert not result.mutations
    assert ops[0].action is OpenLawAction.EXPIRE
    assert [finding.kind for finding in result.findings] == ["open_law_unsupported_codify_action"]
    assert result.findings[0].blocking is False


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


def test_unsupported_codify_action_blocks_in_strict_mode() -> None:
    tree = parse_open_law_xml(_BASE_XML)
    xml = _REPLACE_XML.replace("codify:replace", "codify:expire").replace("</codify:replace>", "</codify:expire>")
    ops = parse_open_law_codify_ops(xml, source_id="editorial-actions/2026-01-22.xml")

    result = replay_open_law_ops(tree, ops, strict=True)

    assert ops[0].action is OpenLawAction.EXPIRE
    assert [finding.kind for finding in result.findings] == ["open_law_unsupported_codify_action"]
    assert result.findings[0].blocking is True


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

    result = audit_open_law_snapshot(before, after, ops)

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

    assert resolved.status == "resolved"
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

    assert resolve_open_law_path(wrapped, ("10", "27", "02", "heading")).status == "resolved"
    assert resolve_open_law_path(wrapped, ("10", "27", "02", "annos")).status == "resolved"


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
    assert report.operation_rows[0].status == "metadata_matched"
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


def test_corpus_audit_records_register_expire_as_lifecycle_lane(tmp_path) -> None:
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

    assert report.summary["lifecycle_unsupported"] == 1
    assert report.operation_rows[0].status == "lifecycle_unsupported"
    assert report.operation_rows[0].expire_date == "2026-11-20"


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
        "snapshot_missing": 0,
        "findings": 0,
        "unexplained_paths": 0,
    }
    (pack_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "summary.json").write_text(json.dumps(summary) + "\n", encoding="utf-8")
    (pack_dir / "operation_audits.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "findings.jsonl").write_text("", encoding="utf-8")
    (pack_dir / "exemplars.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "summary.md").write_text("# Summary\n", encoding="utf-8")
    files = []
    for name in ("manifest.json", "summary.json", "operation_audits.jsonl", "findings.jsonl", "exemplars.json", "summary.md"):
        data = (pack_dir / name).read_bytes()
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    generator = {
        "tool": "lawvm open-law evidence-pack",
        "repository": "/repo",
        "git_commit": "a" * 40,
        "git_dirty": True,
    }
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


def test_open_law_explain_text_includes_evidence_dispositions(tmp_path, capsys) -> None:
    report_dir = tmp_path / "report"
    report_dir.mkdir()
    row = {
        "op_id": "editorial-actions/example.xml:1",
        "status": "lifecycle_unsupported",
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
            "status": "unsupported",
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


def _chapter_xml(text: str) -> str:
    return f"""
    <container xmlns="https://open.law/schemas/library">
      <prefix>Chapter</prefix>
      <num>02</num>
      <heading>Code of Ethics</heading>
      <section><prefix>Regulation</prefix><num>.04</num><heading>Special Responsibilities.</heading><para><num>A.</num><text>{text}</text></para></section>
    </container>
    """


def _index_xml(publication: str, action_paths: tuple[str, ...]) -> str:
    includes = "\n".join(f'<xi:include href="./{path}"/>' for path in action_paths)
    return f"""
    <library xmlns="https://open.law/schemas/library" xmlns:xi="http://www.w3.org/2001/XInclude">
      <meta>
        <build>
          <repositories><repository name="maryland-dsd/law-xml" commit="abcdef1"/></repositories>
          <platform version="test" reproducible="true"/>
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
