from __future__ import annotations

import subprocess
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from lawvm.tools import export_markdown_git as mdgit
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.export_markdown_git import (
    AmendmentCause,
    FastImportCommit,
    MaterializedSnapshot,
    PreparedStatute,
    build_fast_import_stream,
    build_markdown_git_commits,
    build_markdown_git_spool,
    render_act_markdown,
    write_spooled_fast_import_stream,
    write_fast_import_stream,
)
from lawvm.tools.export_transition_graph import ReplayBundle
from lawvm.tools.transition_graph_jurisdictions import TransitionGraphJurisdictionAdapter
from lawvm.tools.transition_graph_profile import TransitionGraphExportProfile
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


def _structured_body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="1 chapter"),
                    IRNode(kind=IRNodeKind.HEADING, text="General"),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 sec"),
                            IRNode(kind=IRNodeKind.HEADING, text="Scope"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(
                                    IRNode(kind=IRNodeKind.CONTENT, text="Intro text."),
                                    IRNode(
                                        kind=IRNodeKind.PARAGRAPH,
                                        label="1",
                                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Item text."),),
                                    ),
                                    IRNode(
                                        kind=IRNodeKind.SUBPARAGRAPH,
                                        label="a",
                                        children=(IRNode(kind=IRNodeKind.CONTENT, text="Subitem text."),),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="2 chapter"),
                    IRNode(kind=IRNodeKind.HEADING, text="Later"),
                ),
            ),
        ),
    )


def _part_chapter_section_body() -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="I",
                children=(
                    IRNode(kind=IRNodeKind.NUM, text="Part I"),
                    IRNode(kind=IRNodeKind.HEADING, text="Framework"),
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="1 chapter"),
                            IRNode(kind=IRNodeKind.HEADING, text="General"),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="1",
                                children=(
                                    IRNode(kind=IRNodeKind.NUM, text="1 sec"),
                                    IRNode(kind=IRNodeKind.HEADING, text="Scope"),
                                ),
                            ),
                        ),
                    ),
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


def test_markdown_render_links_statute_source_and_preserves_stable_toc_anchors() -> None:
    rendered = render_act_markdown(
        statute_id="100/2020",
        title="Test Act",
        version_date="2020-01-01",
        root=_structured_body(),
        tree_hash="abc123",
        source_url="https://example.test/statute/100-2020",
    )

    assert "- Statute: [`100/2020`](<https://example.test/statute/100-2020>)" in rendered
    assert "LawVM Markdown projection. Not an authoritative publication." not in rendered
    assert "- [1 chapter General](#chapter-1)" in rendered
    assert "  - [1 sec Scope](#chapter-1-section-1)" in rendered
    assert "\n\n- [2 chapter Later](#chapter-2)" in rendered


def test_markdown_toc_uses_actual_part_chapter_section_depth() -> None:
    rendered = render_act_markdown(
        statute_id="100/2020",
        title="Test Act",
        version_date="2020-01-01",
        root=_part_chapter_section_body(),
        tree_hash="abc123",
    )

    assert "- [Part I Framework](#part-i)" in rendered
    assert "  - [1 chapter General](#part-i-chapter-1)" in rendered
    assert "    - [1 sec Scope](#part-i-chapter-1-section-1)" in rendered


def test_markdown_render_distinguishes_subsection_and_item_depth() -> None:
    rendered = render_act_markdown(
        statute_id="100/2020",
        title="Test Act",
        version_date="2020-01-01",
        root=_structured_body(),
        tree_hash="abc123",
    )

    assert '<a id="chapter-1-section-1-subsection-1"></a>\n#### \\(1\\)' in rendered
    assert '<a id="chapter-1-section-1-subsection-1-paragraph-1"></a>\n- **1.** Item text\\.' in rendered
    assert '<a id="chapter-1-section-1-subsection-1-subparagraph-a"></a>\n  - **a.** Subitem text\\.' in rendered


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
    assert b"Version: `2020-01-01`" in commits[0].files["acts/2020/100.md"]


def test_build_commits_readme_is_path_ordered_without_sample_counts() -> None:
    root = _body_with_link_text("Stable text.")
    newer = PreparedStatute(
        statute_id="1000/2020",
        engine_id="2020/1000",
        title="Later Number Act",
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
    earlier_number = PreparedStatute(
        statute_id="9/2020",
        engine_id="2020/9",
        title="Earlier Number Act",
        snapshots=(
            MaterializedSnapshot(
                effective_date="2020-01-01",
                root=root,
                tree_hash="hash-three",
            ),
        ),
        interlink_rows=(),
        interlink_targets=(),
    )
    older = PreparedStatute(
        statute_id="1093/1996",
        engine_id="1996/1093",
        title="Older Act",
        snapshots=(
            MaterializedSnapshot(
                effective_date="2020-01-01",
                root=root,
                tree_hash="hash-two",
            ),
        ),
        interlink_rows=(),
        interlink_targets=(),
    )

    commits = build_markdown_git_commits((newer, earlier_number, older), jurisdiction="fi")
    readme = commits[0].files["README.md"].decode("utf-8")
    year_readme = commits[0].files["acts/2020/README.md"].decode("utf-8")

    assert "Active sample statutes" not in readme
    assert "Configured statutes" not in readme
    assert "[1996](acts/1996/)" in readme
    assert "[2020](acts/2020/)" in readme
    assert "(acts/2020/9.md)" not in readme
    assert year_readme.index("(9.md)") < year_readme.index("(1000.md)")


def test_build_commits_records_changed_statutes_and_causes_in_commit_message() -> None:
    prepared = PreparedStatute(
        statute_id="100/2020",
        engine_id="2020/100",
        title="Test Act",
        snapshots=(
            MaterializedSnapshot(
                effective_date="2020-01-01",
                root=_body_with_link_text("Stable text."),
                tree_hash="hash-one",
            ),
        ),
        interlink_rows=(),
        interlink_targets=(),
        amendment_causes_by_date={
            "2020-01-01": (
                AmendmentCause(
                    source_id="200/2019",
                    title="Change Act",
                    source_url="https://example.test/200-2019",
                    kind="change/repeal",
                ),
                AmendmentCause(
                    source_id="300/2018",
                    title="Temporary Act",
                    source_url="https://example.test/300-2018",
                    kind="expiration",
                ),
            ),
        },
    )

    commits = build_markdown_git_commits(
        (prepared,),
        jurisdiction="fi",
        timestamp_zone="Europe/Helsinki",
        generated_at=datetime(2026, 6, 16, 12, 0, tzinfo=ZoneInfo("Europe/Helsinki")),
    )

    assert commits[0].message == (
        "As of 2020-01-01\n\n"
        "Changed statutes:\n"
        "- 100/2020 Test Act\n\n"
        "Causes:\n"
        "- 100/2020 Test Act\n"
        "  - change/repeal: 200/2019 Change Act\n"
        "  - expiration: 300/2018 Temporary Act"
    )
    assert commits[0].timezone_offset == "+0200"
    assert commits[0].committer_timezone_offset == "+0300"
    assert "LawVM:" not in commits[0].message
    assert "generated_at" not in commits[0].message
    stream = build_fast_import_stream(commits)
    assert b"author LawVM <lawvm@example.invalid> " in stream
    assert b" +0200\ncommitter LawVM <lawvm@example.invalid> " in stream
    assert b" +0300\ndata " in stream


def test_amendment_causes_by_date_indexes_effective_and_expiry_sources() -> None:
    profile = TransitionGraphExportProfile(
        jurisdiction="zz",
        lang="zz",
        canonical_statute_id=lambda value: value,
        engine_statute_id=lambda value: value,
        amendment_url=lambda canonical, _engine: f"https://example.test/{canonical}",
    )
    op = SimpleNamespace(
        source=SimpleNamespace(
            statute_id="200/2019",
            title="Change Act",
            effective="2020-01-01",
            expires="2021-01-01",
        )
    )

    causes = mdgit._amendment_causes_by_date((op,), profile=profile)

    assert causes["2020-01-01"] == (
        AmendmentCause(
            source_id="200/2019",
            title="Change Act",
            source_url="https://example.test/200/2019",
            kind="change/repeal",
        ),
    )
    assert causes["2021-01-01"] == (
        AmendmentCause(
            source_id="200/2019",
            title="Change Act",
            source_url="https://example.test/200/2019",
            kind="expiration",
        ),
    )


def test_source_xml_substantive_body_check_accepts_hcontainer_text_and_rejects_empty() -> None:
    assert mdgit._source_xml_has_substantive_body(
        b"<akomaNtoso><act><body><hcontainer><p>TAULUKKO PUUTTUU</p></hcontainer></body></act></akomaNtoso>"
    )
    assert not mdgit._source_xml_has_substantive_body(
        b"<akomaNtoso><act><body><hcontainer /></body></act></akomaNtoso>"
    )
    assert not mdgit._source_xml_has_substantive_body(b"<akomaNtoso><act><meta /></act></akomaNtoso>")


def test_fi_all_replayable_selector_uses_substantive_body_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Corpus:
        def list_statute_ids(self) -> list[str]:
            return ["2020/1000", "2020/9", "1996/1"]

        def read_source(self, statute_id: str) -> bytes | None:
            return {
                "2020/1000": b"<akomaNtoso><act><body><section><num>1</num></section></body></act></akomaNtoso>",
                "2020/9": b"<akomaNtoso><act><body><hcontainer /></body></act></akomaNtoso>",
                "1996/1": b"<akomaNtoso><act><body><hcontainer><p>Body</p></hcontainer></body></act></akomaNtoso>",
            }[statute_id]

    def canonical_statute_id(value: str) -> str:
        year, number = value.split("/", 1)
        return f"{number}/{year}"

    profile = TransitionGraphExportProfile(
        jurisdiction="fi",
        lang="fi",
        canonical_statute_id=canonical_statute_id,
        engine_statute_id=lambda value: value,
        corpus=Corpus,
    )
    adapter = TransitionGraphJurisdictionAdapter(
        profile=profile,
        replay_runner=lambda _engine_id, *, profile: None,
        tree_materializer=lambda _bundle, _as_of: None,
        interlink_provider=None,
    )
    monkeypatch.setattr(mdgit, "transition_graph_adapter_for_jurisdiction", lambda _jurisdiction: adapter)

    assert mdgit.statute_ids_from_fi_corpus() == ("1/1996", "1000/2020")


def test_spooled_export_streams_incremental_repo(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {
        "100/2020": {
            "2020-01-01": _body_with_link_text("Alpha text."),
            "2021-01-01": _body_with_link_text("Beta text."),
        },
        "200/2020": {
            "2020-01-01": _body_with_link_text("Second act."),
        },
        "9/2020": {
            "2020-01-01": _body_with_link_text("Single digit act."),
        },
    }

    def replay_runner(engine_id: str, *, profile: TransitionGraphExportProfile) -> ReplayBundle:
        return ReplayBundle(
            statute_id=profile.canonical_statute_id(engine_id),
            engine_id=engine_id,
            title=f"Act {engine_id}",
            result=SimpleNamespace(),
            lo_ops=[],
            timelines={},
            change_dates=list(roots[engine_id]),
        )

    def materializer(bundle: ReplayBundle, as_of: str) -> IRNode:
        return roots[bundle.engine_id][as_of]

    profile = TransitionGraphExportProfile(
        jurisdiction="zz",
        lang="zz",
        canonical_statute_id=lambda value: value,
        engine_statute_id=lambda value: value,
        corpus=lambda: None,
    )
    adapter = TransitionGraphJurisdictionAdapter(
        profile=profile,
        replay_runner=replay_runner,
        tree_materializer=materializer,
        interlink_provider=None,
    )
    monkeypatch.setattr(mdgit, "transition_graph_adapter_for_jurisdiction", lambda _jurisdiction: adapter)
    spool = tmp_path / "spool.db"

    build_stats = build_markdown_git_spool(
        ("100/2020", "200/2020", "9/2020"),
        jurisdiction="zz",
        db_path=spool,
        workers=1,
    )
    repo = tmp_path / "spooled.git"
    export_stats = write_spooled_fast_import_stream(
        spool,
        jurisdiction="zz",
        out_path=None,
        repo_path=repo,
        force=False,
        timestamp_zone="Europe/Helsinki",
        generated_at=datetime(2026, 6, 16, 12, 0, tzinfo=ZoneInfo("Europe/Helsinki")),
    )
    latest_first = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:acts/2020/100.md"],
        text=True,
    )
    latest_second = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:acts/2020/200.md"],
        text=True,
    )
    readme = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:README.md"],
        text=True,
    )
    year_readme = subprocess.check_output(
        ["git", "--git-dir", str(repo), "show", "in-force:acts/2020/README.md"],
        text=True,
    )
    log_subjects = subprocess.check_output(
        ["git", "--git-dir", str(repo), "log", "--format=%s", "in-force"],
        text=True,
    )
    raw_dates = subprocess.check_output(
        ["git", "--git-dir", str(repo), "log", "--format=%ad %cd", "--date=raw", "in-force"],
        text=True,
    )

    assert build_stats.statute_count == 3
    assert build_stats.version_count == 4
    assert export_stats.commit_count == 2
    assert "Beta text" in latest_first
    assert "Second act" in latest_second
    assert "Active sample statutes" not in readme
    assert "Configured statutes" not in readme
    assert "[2020](acts/2020/)" in readme
    assert "(acts/2020/9.md)" not in readme
    assert year_readme.index("(9.md)") < year_readme.index("(100.md)")
    assert year_readme.index("(100.md)") < year_readme.index("(200.md)")
    assert "As of 2021-01-01" in log_subjects
    assert "As of 2020-01-01" in log_subjects
    assert " +0200 " in raw_dates
    assert " +0300" in raw_dates


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


def test_prepare_markdown_git_export_includes_future_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    roots = {
        "2020-01-01": _body_with_link_text("Current text."),
        "2999-01-01": _body_with_link_text("Prospective text."),
    }

    def replay_runner(_engine_id: str, *, profile: TransitionGraphExportProfile) -> ReplayBundle:
        return ReplayBundle(
            statute_id="100/2020",
            engine_id="100/2020",
            change_dates=("2020-01-01", "2999-01-01"),
            title="Test Act",
            result=SimpleNamespace(),
            lo_ops=[],
            timelines={},
        )

    def materializer(_bundle: SimpleNamespace, as_of: str) -> IRNode:
        return roots[as_of]

    profile = TransitionGraphExportProfile(
        jurisdiction="zz",
        lang="zz",
        canonical_statute_id=lambda value: value,
        engine_statute_id=lambda value: value,
        corpus=lambda: None,
    )
    adapter = TransitionGraphJurisdictionAdapter(
        profile=profile,
        replay_runner=replay_runner,
        tree_materializer=materializer,
        interlink_provider=None,
    )
    monkeypatch.setattr(mdgit, "transition_graph_adapter_for_jurisdiction", lambda _jurisdiction: adapter)

    prepared = mdgit.prepare_markdown_git_export(("100/2020",), jurisdiction="zz")
    capped = mdgit.prepare_markdown_git_export(
        ("100/2020",),
        jurisdiction="zz",
        include_future=False,
        until="2020-12-31",
    )

    assert prepared[0].dates == ("2020-01-01", "2999-01-01")
    assert capped[0].dates == ("2020-01-01",)
