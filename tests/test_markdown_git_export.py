from __future__ import annotations

import subprocess

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.export_markdown_git import (
    FastImportCommit,
    MaterializedSnapshot,
    PreparedStatute,
    build_fast_import_stream,
    build_markdown_git_commits,
    render_act_markdown,
    write_fast_import_stream,
)
from lawvm.tools.transition_graph_interlinks import LawvmInterlinkRow


def _row_with_rendered_link(*, text: str, start: int, end: int) -> LawvmInterlinkRow:
    return LawvmInterlinkRow(
        interlink_id="link-1",
        source_jurisdiction="fi",
        source_work_kind="normative_act",
        source_local_id="100/2020",
        source_work_id="fi:normative_act:2020/100",
        source_locator="section:1",
        surface_text=text[start:end],
        surface_kind="prose_ref",
        role="cites",
        target_jurisdiction="fi",
        target_work_kind="normative_act",
        target_local_id="9/2023",
        target_work_id="fi:normative_act:2023/9",
        target_locator="chapter:1/section:2",
        target_url="https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_2",
        candidate_work_ids=None,
        resolution_status="resolved",
        confidence="exact",
        resolver_id="test",
        source_artifact_id=None,
        source_span_byte_offset=None,
        source_span_byte_len=None,
        rendered_statute_id="100/2020",
        rendered_effective_date="2020-01-01",
        rendered_address="section:1",
        rendered_segment_index=0,
        rendered_char_start=start,
        rendered_char_end=end,
        valid_at_start=None,
        valid_at_end=None,
        detail_json="{}",
    )


def _body_with_link_text(text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 sec"),
                    IRNode(kind=IRNodeKind.HEADING, text="Scope"),
                    IRNode(kind=IRNodeKind.CONTENT, text=text),
                ),
            ),
        ),
    )


def test_markdown_render_adds_github_anchors_and_inline_links() -> None:
    text = "Reference to 9/2023 remains readable."
    start = text.index("9/2023")
    end = start + len("9/2023")

    rendered = render_act_markdown(
        statute_id="100/2020",
        title="Test Act",
        version_date="2020-01-01",
        root=_body_with_link_text(text),
        tree_hash="abc123",
        interlink_rows=(_row_with_rendered_link(text=text, start=start, end=end),),
        interlink_targets=(),
    )

    assert '<a id="section-1"></a>' in rendered
    assert "### 1 sec Scope" in rendered
    assert (
        "[9/2023](<https://www.finlex.fi/fi/lainsaadanto/2023/9#chp_1__sec_2>)"
        in rendered
    )


def test_fast_import_stream_imports_into_bare_repo(tmp_path) -> None:
    stream = build_fast_import_stream(
        (
            FastImportCommit(
                effective_date="2020-01-01",
                message="As of 2020-01-01",
                files={
                    "README.md": b"# Snapshot\n",
                    "acts/2020/100.md": b"# Test Act\n\nalpha\n",
                },
                timestamp=1_577_836_800,
            ),
            FastImportCommit(
                effective_date="2021-01-01",
                message="As of 2021-01-01",
                files={
                    "README.md": b"# Snapshot\n",
                    "acts/2020/100.md": b"# Test Act\n\nbeta\n",
                },
                timestamp=1_609_459_200,
            ),
        )
    )

    repo = tmp_path / "out.git"
    subprocess.run(["git", "init", "--bare", str(repo)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(repo), "fast-import", "--date-format=raw"],
        input=stream,
        check=True,
    )

    latest = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:acts/2020/100.md"],
        text=True,
    )
    log_subjects = subprocess.check_output(
        ["git", "--git-dir", str(repo), "log", "--format=%s", "in-force"],
        text=True,
    )
    tag_target = subprocess.check_output(
        ["git", "--git-dir", str(repo), "rev-parse", "current"],
        text=True,
    ).strip()
    in_force_target = subprocess.check_output(
        ["git", "--git-dir", str(repo), "rev-parse", "in-force"],
        text=True,
    ).strip()

    assert "beta" in latest
    assert "As of 2021-01-01" in log_subjects
    assert "As of 2020-01-01" in log_subjects
    assert tag_target == in_force_target


def test_write_fast_import_stream_can_create_bare_repo(tmp_path) -> None:
    repo = tmp_path / "direct.git"

    stats = write_fast_import_stream(
        (
            FastImportCommit(
                effective_date="2020-01-01",
                message="As of 2020-01-01",
                files={"README.md": b"# Direct\n"},
                timestamp=1_577_836_800,
            ),
        ),
        out_path=None,
        repo_path=repo,
        force=False,
    )
    readme = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:README.md"],
        text=True,
    )
    head_ref = subprocess.check_output(
        ["git", "--git-dir", str(repo), "symbolic-ref", "HEAD"],
        text=True,
    )

    assert stats.destination == str(repo)
    assert stats.byte_count > 0
    assert "# Direct" in readme
    assert head_ref.strip() == "refs/heads/in-force"


def test_build_commits_keeps_unchanged_statute_file_bytes_stable() -> None:
    root = _body_with_link_text("Stable text.")
    prepared = PreparedStatute(
        statute_id="100/2020",
        engine_id="2020/100",
        title="Test Act",
        snapshots=(
            MaterializedSnapshot(
                effective_date="2020-01-01",
                root=root,
                tree_hash="hash-one",
            ),
        ),
        interlink_rows=(),
        interlink_targets=(),
    )

    commits = build_markdown_git_commits((prepared,), jurisdiction="fi")

    assert len(commits) == 1
    assert b"Rendered statute version: `2020-01-01`" in commits[0].files["acts/2020/100.md"]


def test_build_commits_uses_jurisdiction_fallback_for_non_num_year_ids() -> None:
    prepared = PreparedStatute(
        statute_id="ukpga/2020/1",
        engine_id="ukpga/2020/1",
        title="UK Test Act",
        snapshots=(
            MaterializedSnapshot(
                effective_date="2020-01-01",
                root=_body_with_link_text("Stable text."),
                tree_hash="hash-one",
            ),
        ),
        interlink_rows=(),
        interlink_targets=(),
    )

    commits = build_markdown_git_commits((prepared,), jurisdiction="uk")

    assert "acts/uk/ukpga__2020__1.md" in commits[0].files
