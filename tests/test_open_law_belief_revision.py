"""Tests for the Open Law cross-branch belief-revision coherence check.

The belief-revision auditor is the first genuinely *independent* Open Law check:
it compares two publication branches that publish beliefs about the SAME legal
slice at different observer times and adjudicates every divergence against the
source lane. A divergence with a declared source-lane editorial cause is an
EXPLAINED revision; a divergence with no such cause -- classically, when both
branches share a source commit -- is a first-class ``open_law_cross_branch_silent_revision``
finding, the exact thing a self-attesting compiled lane cannot catch about
itself.
"""

from __future__ import annotations

from argparse import Namespace
import json
import os
import subprocess
from pathlib import Path

import pytest

from lawvm.open_law.belief_revision import (
    audit_maryland_belief_revisions,
    belief_report_to_jsonable,
    write_belief_revision_report,
)
from lawvm.open_law.local_git import make_maryland_repos
from lawvm.tools.open_law import _print_belief_revision


# Real Maryland Open Law clones are supplied via environment variables so no
# developer-local path is baked into a tracked file (release-hygiene gate). The
# real-repo regression is skipped when they are unset or absent.
_REAL_CODIFIED_ENV = "LAWVM_OPEN_LAW_CODIFIED_REPO"
_REAL_SOURCE_ENV = "LAWVM_OPEN_LAW_SOURCE_REPO"


def _real_repo_paths() -> tuple[Path, Path] | None:
    codified = os.environ.get(_REAL_CODIFIED_ENV)
    source = os.environ.get(_REAL_SOURCE_ENV)
    if not codified or not source:
        return None
    codified_path = Path(codified)
    source_path = Path(source)
    if not codified_path.exists() or not source_path.exists():
        return None
    return source_path, codified_path


def _chapter_xml(text: str) -> str:
    return f"""
    <container xmlns="https://open.law/schemas/library">
      <prefix>Chapter</prefix>
      <num>02</num>
      <heading>Code of Ethics</heading>
      <section><prefix>Regulation</prefix><num>.04</num><heading>Special Responsibilities.</heading><para><num>A.</num><text>{text}</text></para></section>
    </container>
    """


_REPLACE_XML = """<?xml version='1.0' encoding='utf-8'?>
<document xmlns="https://open.law/schemas/library"
    xmlns:codify="https://open.law/schemas/codify"
    id="Editor Action 2026-01-22">
  <meta><effective>2026-01-22</effective></meta>
  <codify:replace history="false" doc="Code of Maryland Regulations" path="10|41|02|.04">
    <section>
      <prefix>Regulation</prefix>
      <num>.04</num>
      <heading>Special Responsibilities.</heading>
      <para><num>A.</num><text>New text.</text></para>
    </section>
  </codify:replace>
</document>
"""


def _index_xml(publication: str, *, source_commit: str) -> str:
    return f"""
    <library xmlns="https://open.law/schemas/library" xmlns:xi="http://www.w3.org/2001/XInclude">
      <meta>
        <build>
          <repositories><repository name="maryland-dsd/law-xml" commit="{source_commit}"/></repositories>
          <platform version="test" reproducible="true"/>
          <build-date>2026-01-01</build-date>
          <codified-date>2026-01-01</codified-date>
          <publication>{publication}</publication>
        </build>
      </meta>
      <xi:include href="./us/md/exec/comar/index.xml"/>
    </library>
    """


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git_init(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(path)), check=True)
    subprocess.run(("git", "-C", str(path), "checkout", "-q", "-b", "main"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.email", "test@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(path), "config", "user.name", "Test"), check=True)


def _git_commit_all(path: Path, message: str) -> str:
    subprocess.run(("git", "-C", str(path), "add", "."), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-q", "-m", message), check=True)
    return subprocess.check_output(("git", "-C", str(path), "rev-parse", "HEAD"), text=True).strip()


def _git_branch(path: Path, branch: str) -> None:
    subprocess.run(("git", "-C", str(path), "branch", branch), check=True)


def _comar_path(codified: Path) -> Path:
    return codified / "us/md/exec/comar/10/41/02.xml"


def test_same_source_commit_belief_delta_is_silent_revision(tmp_path) -> None:
    # Two branches publish beliefs about the SAME legal slice from the SAME
    # source commit, yet their codified legal text differs. No editorial action
    # can differ (source is byte-identical), so this is a silent revision.
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Belief one."))
    _git_commit_all(codified_repo, "earlier belief")
    _git_branch(codified_repo, "publication/2026-01-01")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Belief two, silently revised."))
    _git_commit_all(codified_repo, "later belief")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-08")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_belief_revisions(repos=repos)

    assert report.summary["pairs_audited"] == 1
    assert report.summary["pairs_same_source_commit"] == 1
    assert report.summary["silent_revisions"] == 1
    assert report.summary["explained_revisions"] == 0
    finding = report.pair_reports[0].findings[0]
    assert finding.kind == "open_law_cross_branch_silent_revision"
    assert finding.explained is False
    assert finding.declared_causes == ()
    # Self-evidencing: carries both belief hashes and they differ.
    assert finding.earlier_belief_sha256 != finding.later_belief_sha256
    assert finding.earlier_source_commit == finding.later_source_commit == source_commit
    assert finding.comar_locator == ("10", "41", "02")
    assert finding.blocking is False


def test_strict_marks_silent_revision_blocking(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Belief one."))
    _git_commit_all(codified_repo, "earlier")
    _git_branch(codified_repo, "publication/2026-01-01")
    _write(_comar_path(codified_repo), _chapter_xml("Belief two."))
    _git_commit_all(codified_repo, "later")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-08")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_belief_revisions(repos=repos, strict=True)
    assert report.pair_reports[0].findings[0].blocking is True


def test_declared_editorial_action_explains_belief_revision(tmp_path) -> None:
    # The later branch was built from a NEW source commit that introduced an
    # editorial action targeting the changed chapter. The belief delta is then
    # EXPLAINED, not silent.
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "old.xml", _REPLACE_XML.replace("New text.", "Old editorial text."))
    earlier_commit = _git_commit_all(source_repo, "source before")
    _write(source_repo / "editorial-actions" / "new.xml", _REPLACE_XML)
    later_commit = _git_commit_all(source_repo, "source after adds editorial action")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-02-01", source_commit=earlier_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Old text."))
    _git_commit_all(codified_repo, "earlier belief")
    _git_branch(codified_repo, "publication/2026-02-01")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-02-01", source_commit=later_commit))
    _write(_comar_path(codified_repo), _chapter_xml("New text."))
    _git_commit_all(codified_repo, "later belief")
    _git_branch(codified_repo, "publication/2026-02-01.2026-02-08")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_belief_revisions(repos=repos)

    assert report.summary["silent_revisions"] == 0
    assert report.summary["explained_revisions"] == 1
    finding = report.pair_reports[0].findings[0]
    assert finding.kind == "open_law_cross_branch_belief_revision_explained"
    assert finding.explained is True
    assert len(finding.declared_causes) == 1
    cause = finding.declared_causes[0]
    assert cause.action == "replace"
    assert cause.codify_path == ("10", "41", "02", ".04")
    assert cause.source_id.endswith("new.xml")


def test_projection_only_delta_is_not_a_revision(tmp_path) -> None:
    # The git blobs differ only in projected-out layers (curly vs straight
    # quotes here); once projected to comparison IR the two beliefs are equal,
    # so no belief-revision finding is emitted.
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")

    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Quote 'same' text."))
    _git_commit_all(codified_repo, "earlier")
    _git_branch(codified_repo, "publication/2026-01-01")
    _write(_comar_path(codified_repo), _chapter_xml("Quote ‘same’ text."))
    _git_commit_all(codified_repo, "later")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-08")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_belief_revisions(repos=repos)

    assert report.pair_reports[0].documents_compared == 1  # blob differed
    assert report.summary["documents_diverged"] == 0  # but beliefs are equal
    assert report.summary["silent_revisions"] == 0


def test_single_belief_slice_has_no_pair(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")
    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("Sole belief."))
    _git_commit_all(codified_repo, "only belief")
    _git_branch(codified_repo, "publication/2026-01-01")

    report = audit_maryland_belief_revisions(repos=make_maryland_repos(source_repo, codified_repo))
    assert report.summary["pairs_audited"] == 0
    assert report.summary["silent_revisions"] == 0


def test_belief_report_jsonable_and_writer(tmp_path) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")
    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("One."))
    _git_commit_all(codified_repo, "earlier")
    _git_branch(codified_repo, "publication/2026-01-01")
    _write(_comar_path(codified_repo), _chapter_xml("Two."))
    _git_commit_all(codified_repo, "later")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-08")

    repos = make_maryland_repos(source_repo, codified_repo)
    report = audit_maryland_belief_revisions(repos=repos)
    # The jsonable payload round-trips through JSON (self-evidencing serialization)
    # and re-parses to a plain dict, so nested access is on concrete dict/list.
    payload = json.loads(json.dumps(belief_report_to_jsonable(report)))
    assert payload["summary"]["silent_revisions"] == 1
    assert payload["pairs"][0]["findings"][0]["kind"] == "open_law_cross_branch_silent_revision"

    out_dir = tmp_path / "out"
    write_belief_revision_report(report, out_dir)
    summary = json.loads((out_dir / "belief_revision_summary.json").read_text(encoding="utf-8"))
    assert summary["silent_revisions"] == 1
    rows = [json.loads(line) for line in (out_dir / "belief_revisions.jsonl").read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["earlier_belief_sha256"] != rows[0]["later_belief_sha256"]


def test_cli_belief_revision_smoke(tmp_path, capsys) -> None:
    source_repo = tmp_path / "law-xml"
    codified_repo = tmp_path / "law-xml-codified"
    _git_init(source_repo)
    _git_init(codified_repo)
    _write(source_repo / "editorial-actions" / "a.xml", _REPLACE_XML)
    source_commit = _git_commit_all(source_repo, "source")
    _write(codified_repo / "index.xml", _index_xml("publication/2026-01-01", source_commit=source_commit))
    _write(_comar_path(codified_repo), _chapter_xml("One."))
    _git_commit_all(codified_repo, "earlier")
    _git_branch(codified_repo, "publication/2026-01-01")
    _write(_comar_path(codified_repo), _chapter_xml("Two."))
    _git_commit_all(codified_repo, "later")
    _git_branch(codified_repo, "publication/2026-01-01.2026-01-08")

    args = Namespace(
        source_repo=str(source_repo),
        codified_repo=str(codified_repo),
        out=str(tmp_path / "report"),
        limit=None,
        strict=False,
        json=False,
    )
    _print_belief_revision(args)
    out = capsys.readouterr().out
    assert "silent_revisions=1" in out
    assert "open_law_cross_branch_silent_revision" in out


def test_real_repo_belief_revision_finds_silent_revision() -> None:
    # Real-corpus regression on the confirmed differing pair: publication/2025-11-18
    # vs publication/2025-11-18.2025-12-04. Both were built from the SAME source
    # commit yet the later belief rewrote whole regulations, so the auditor must
    # convict at least one silent revision on the real clones.
    paths = _real_repo_paths()
    if paths is None:
        pytest.skip(f"set {_REAL_SOURCE_ENV} and {_REAL_CODIFIED_ENV} to real Maryland Open Law clones")
    source_path, codified_path = paths
    repos = make_maryland_repos(source_path, codified_path)
    # Audit only the confirmed pair by targeting its legal slice via a small
    # diverged budget; correctness is that a real silent revision surfaces.
    report = audit_maryland_belief_revisions(repos=repos, limit=1)
    assert report.summary["silent_revisions"] >= 1
    silent = [
        finding
        for pair in report.pair_reports
        for finding in pair.findings
        if finding.kind == "open_law_cross_branch_silent_revision"
    ]
    assert silent
    example = silent[0]
    assert example.earlier_belief_sha256 != example.later_belief_sha256
    assert example.xml_path.startswith("us/md/exec/comar/")
