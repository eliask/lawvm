"""Export LawVM materializations as a git-fast-import Markdown stream."""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import json
import multiprocessing as mp
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO
from zoneinfo import ZoneInfo

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.tools.export_transition_graph import ReplayBundle
from lawvm.tools.transition_graph_interlinks import (
    InterlinkTargetPreviewContext,
    LawvmInterlinkRow,
    LawvmInterlinkTargetRow,
    enrich_lawvm_interlink_targets,
    place_lawvm_interlinks,
    rendered_text_segments,
)
from lawvm.tools.transition_graph_jurisdictions import transition_graph_adapter_for_jurisdiction

_ADDRESSABLE_KINDS = frozenset(
    {
        "part",
        "chapter",
        "section",
        "subsection",
        "paragraph",
        "subparagraph",
    }
)
_HEADING_LEVEL_BY_KIND = {
    "part": 2,
    "chapter": 2,
    "section": 3,
}
_LOWER_ADDRESSABLE_KINDS = frozenset({"subsection", "paragraph", "subparagraph"})
_SKIP_INLINE_CHILD_KINDS = _ADDRESSABLE_KINDS | {"num", "heading"}
_MARKDOWN_ESCAPE_RE = re.compile(r"([\\`*_{}\[\]()#+.!|<>-])")
_ANCHOR_CLEANUP_RE = re.compile(r"[^a-z0-9]+")
_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9._/-]+")
_DEFAULT_BRANCH = "in-force"


@dataclasses.dataclass(frozen=True, slots=True)
class MaterializedSnapshot:
    effective_date: str
    root: IRNode
    tree_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class AmendmentCause:
    source_id: str
    title: str
    source_url: str
    kind: str


@dataclasses.dataclass(frozen=True, slots=True)
class PreparedStatute:
    statute_id: str
    engine_id: str
    title: str
    snapshots: tuple[MaterializedSnapshot, ...]
    interlink_rows: tuple[LawvmInterlinkRow, ...]
    interlink_targets: tuple[LawvmInterlinkTargetRow, ...]
    source_url: str = ""
    amendment_causes_by_date: Mapping[str, tuple[AmendmentCause, ...]] = dataclasses.field(
        default_factory=dict
    )

    @property
    def dates(self) -> tuple[str, ...]:
        return tuple(snapshot.effective_date for snapshot in self.snapshots)

    def snapshot_at_or_before(self, as_of: str) -> MaterializedSnapshot | None:
        dates = self.dates
        index = bisect.bisect_right(dates, as_of) - 1
        if index < 0:
            return None
        return self.snapshots[index]


@dataclasses.dataclass(frozen=True, slots=True)
class FastImportCommit:
    effective_date: str
    message: str
    files: Mapping[str, bytes]
    timestamp: int
    timezone_offset: str = "+0000"
    committer_timestamp: int | None = None
    committer_timezone_offset: str = "+0000"


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitExportStats:
    statute_count: int
    commit_count: int
    file_count: int
    byte_count: int
    destination: str


@dataclasses.dataclass(frozen=True, slots=True)
class RenderedMarkdownVersion:
    effective_date: str
    tree_hash: str
    content: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class RenderedMarkdownStatute:
    statute_id: str
    engine_id: str
    title: str
    path: str
    source_url: str
    versions: tuple[RenderedMarkdownVersion, ...]
    amendment_causes_by_date: Mapping[str, tuple[AmendmentCause, ...]]


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitRenderRequest:
    statute_id: str
    jurisdiction: str
    include_future: bool
    until: str | None


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitSpoolBuildStats:
    statute_count: int
    version_count: int
    byte_count: int
    spool_path: Path


@dataclasses.dataclass(frozen=True, slots=True)
class SpoolStatuteRow:
    statute_id: str
    title: str
    path: str


@dataclasses.dataclass(frozen=True, slots=True)
class SpoolVersionRow:
    statute_id: str
    title: str
    path: str
    effective_date: str
    content: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class SpoolStatuteChange:
    statute_id: str
    title: str
    causes: tuple[AmendmentCause, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_manifest_path() -> Path:
    return _repo_root() / "viewer" / "statute-timeline-manifest.json"


def statute_ids_from_manifest(path: Path, *, jurisdiction: str) -> tuple[str, ...]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids: list[str] = []
    if not isinstance(data, list):
        raise ValueError(f"manifest must be a JSON array: {path}")
    for row in data:
        if isinstance(row, dict):
            statute_id = str(row.get("statute_id") or "").strip()
            row_jurisdiction = str(row.get("jurisdiction") or "").strip().lower()
            if statute_id and row_jurisdiction == jurisdiction:
                ids.append(statute_id)
    return tuple(ids)


def prepare_markdown_git_export(
    statute_ids: Iterable[str],
    *,
    jurisdiction: str,
    include_future: bool = True,
    until: str | None = None,
) -> tuple[PreparedStatute, ...]:
    adapter = transition_graph_adapter_for_jurisdiction(jurisdiction)
    profile = adapter.profile
    cutoff = None if include_future and until is None else (until or date.today().isoformat())
    corpus = profile.corpus()
    prepared: list[PreparedStatute] = []
    for raw_id in statute_ids:
        canonical_id = profile.canonical_statute_id(raw_id)
        engine_id = profile.engine_statute_id(canonical_id)
        bundle: ReplayBundle = adapter.replay_runner(engine_id, profile=profile)
        snapshots: list[MaterializedSnapshot] = []
        for effective_date in bundle.change_dates:
            if cutoff is not None and effective_date > cutoff:
                continue
            root = adapter.tree_materializer(bundle, effective_date)
            snapshots.append(
                MaterializedSnapshot(
                    effective_date=effective_date,
                    root=root,
                    tree_hash=structural_subtree_hash(root),
                )
            )
        if not snapshots:
            continue
        rows, targets = _prepare_interlinks(
            statute_id=canonical_id,
            corpus=corpus,
            roots_by_date={snapshot.effective_date: snapshot.root for snapshot in snapshots},
            interlink_provider=adapter.interlink_provider,
        )
        prepared.append(
            PreparedStatute(
                statute_id=canonical_id,
                engine_id=engine_id,
                title=str(bundle.title or canonical_id),
                source_url=profile.statute_url(canonical_id, engine_id),
                snapshots=tuple(snapshots),
                interlink_rows=tuple(rows),
                interlink_targets=tuple(targets),
                amendment_causes_by_date=_amendment_causes_by_date(
                    bundle.lo_ops,
                    profile=profile,
                ),
            )
        )
    return tuple(prepared)


def render_markdown_git_statute(request: MarkdownGitRenderRequest) -> RenderedMarkdownStatute | None:
    prepared = prepare_markdown_git_export(
        (request.statute_id,),
        jurisdiction=request.jurisdiction,
        include_future=request.include_future,
        until=request.until,
    )
    if not prepared:
        return None
    statute = prepared[0]
    path = _statute_markdown_path(statute.statute_id, jurisdiction=request.jurisdiction)
    versions: list[RenderedMarkdownVersion] = []
    for snapshot in statute.snapshots:
        markdown = render_act_markdown(
            statute_id=statute.statute_id,
            title=statute.title,
            version_date=snapshot.effective_date,
            root=snapshot.root,
            tree_hash=snapshot.tree_hash,
            source_url=statute.source_url,
            interlink_rows=statute.interlink_rows,
            interlink_targets=statute.interlink_targets,
        )
        versions.append(
            RenderedMarkdownVersion(
                effective_date=snapshot.effective_date,
                tree_hash=snapshot.tree_hash,
                content=_ensure_lf(markdown).encode("utf-8"),
            )
        )
    return RenderedMarkdownStatute(
        statute_id=statute.statute_id,
        engine_id=statute.engine_id,
        title=statute.title,
        path=path,
        source_url=statute.source_url,
        versions=tuple(versions),
        amendment_causes_by_date=statute.amendment_causes_by_date,
    )


def build_markdown_git_spool(
    statute_ids: Iterable[str],
    *,
    jurisdiction: str,
    db_path: Path,
    include_future: bool = True,
    until: str | None = None,
    workers: int = 1,
) -> MarkdownGitSpoolBuildStats:
    ids = tuple(statute_ids)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect_markdown_spool(db_path)
    with conn:
        _create_markdown_spool_schema(conn)
    statute_count = 0
    version_count = 0
    byte_count = 0
    requests = tuple(
        MarkdownGitRenderRequest(
            statute_id=statute_id,
            jurisdiction=jurisdiction,
            include_future=include_future,
            until=until,
        )
        for statute_id in ids
    )
    for rendered in _iter_rendered_markdown_statutes(requests, workers=workers):
        if rendered is None:
            continue
        with conn:
            _insert_rendered_statute(conn, rendered)
        statute_count += 1
        version_count += len(rendered.versions)
        byte_count += sum(len(version.content) for version in rendered.versions)
    conn.execute("VACUUM")
    conn.close()
    return MarkdownGitSpoolBuildStats(
        statute_count=statute_count,
        version_count=version_count,
        byte_count=byte_count,
        spool_path=db_path,
    )


def _iter_rendered_markdown_statutes(
    requests: tuple[MarkdownGitRenderRequest, ...],
    *,
    workers: int,
) -> Iterator[RenderedMarkdownStatute | None]:
    if workers <= 1:
        for request in requests:
            yield render_markdown_git_statute(request)
        return
    with mp.Pool(processes=workers) as pool:
        yield from pool.imap_unordered(render_markdown_git_statute, requests)


def _connect_markdown_spool(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _create_markdown_spool_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE statutes (
          statute_id TEXT PRIMARY KEY,
          engine_id TEXT NOT NULL,
          title TEXT NOT NULL,
          path TEXT NOT NULL UNIQUE,
          source_url TEXT NOT NULL
        );

        CREATE TABLE versions (
          statute_id TEXT NOT NULL,
          effective_date TEXT NOT NULL,
          tree_hash TEXT NOT NULL,
          path TEXT NOT NULL,
          content BLOB NOT NULL,
          PRIMARY KEY (effective_date, statute_id),
          FOREIGN KEY (statute_id) REFERENCES statutes(statute_id)
        );

        CREATE TABLE causes (
          statute_id TEXT NOT NULL,
          effective_date TEXT NOT NULL,
          kind TEXT NOT NULL,
          source_id TEXT NOT NULL,
          title TEXT NOT NULL,
          source_url TEXT NOT NULL,
          PRIMARY KEY (statute_id, effective_date, kind, source_id),
          FOREIGN KEY (statute_id) REFERENCES statutes(statute_id)
        );

        CREATE INDEX versions_by_statute_date ON versions(statute_id, effective_date);
        CREATE INDEX causes_by_date_statute ON causes(effective_date, statute_id);
        """
    )


def _insert_rendered_statute(conn: sqlite3.Connection, statute: RenderedMarkdownStatute) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO statutes(statute_id, engine_id, title, path, source_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (statute.statute_id, statute.engine_id, statute.title, statute.path, statute.source_url),
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO versions(statute_id, effective_date, tree_hash, path, content)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            (statute.statute_id, version.effective_date, version.tree_hash, statute.path, version.content)
            for version in statute.versions
        ),
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO causes(statute_id, effective_date, kind, source_id, title, source_url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            (
                statute.statute_id,
                effective_date,
                cause.kind,
                cause.source_id,
                cause.title,
                cause.source_url,
            )
            for effective_date, causes in statute.amendment_causes_by_date.items()
            for cause in causes
        ),
    )


def _amendment_causes_by_date(
    lo_ops: Iterable[Any],
    *,
    profile: Any,
) -> dict[str, tuple[AmendmentCause, ...]]:
    causes: dict[str, dict[tuple[str, str], AmendmentCause]] = {}
    for op in lo_ops:
        src = op.source
        if src is None or not src.statute_id:
            continue
        source_id = profile.canonical_statute_id(src.statute_id)
        engine_id = profile.engine_statute_id(source_id)
        title = str(src.title or "")
        source_url = profile.amendment_url(source_id, engine_id)
        if src.effective:
            _add_amendment_cause(
                causes,
                str(src.effective),
                AmendmentCause(
                    source_id=source_id,
                    title=title,
                    source_url=source_url,
                    kind="change/repeal",
                ),
            )
        if src.expires:
            _add_amendment_cause(
                causes,
                str(src.expires),
                AmendmentCause(
                    source_id=source_id,
                    title=title,
                    source_url=source_url,
                    kind="expiration",
                ),
            )
    return {
        effective_date: tuple(sorted(by_key.values(), key=lambda cause: (cause.kind, cause.source_id)))
        for effective_date, by_key in sorted(causes.items())
    }


def _add_amendment_cause(
    causes: dict[str, dict[tuple[str, str], AmendmentCause]],
    effective_date: str,
    cause: AmendmentCause,
) -> None:
    causes.setdefault(effective_date, {}).setdefault((cause.kind, cause.source_id), cause)


def _prepare_interlinks(
    *,
    statute_id: str,
    corpus: object | None,
    roots_by_date: Mapping[str, IRNode],
    interlink_provider: Any,
) -> tuple[list[LawvmInterlinkRow], list[LawvmInterlinkTargetRow]]:
    if interlink_provider is None:
        return [], []
    projected = interlink_provider.project_interlinks(statute_id, corpus)
    target_resolver = None
    if interlink_provider.resolve_target is not None:
        preview_context = InterlinkTargetPreviewContext(
            source_statute_id=statute_id,
            corpus=corpus,
        )

        def target_resolver(target_ref: Any) -> Any:
            return interlink_provider.resolve_target(target_ref, preview_context)

    rows, targets = enrich_lawvm_interlink_targets(projected, target_resolver=target_resolver)
    segments_by_date = {
        effective_date: rendered_text_segments(effective_date, root, "")
        for effective_date, root in roots_by_date.items()
    }
    rows = place_lawvm_interlinks(
        rows,
        statute_id=statute_id,
        segments_by_date=segments_by_date,
    )
    return rows, targets


def build_markdown_git_commits(
    statutes: Iterable[PreparedStatute],
    *,
    jurisdiction: str,
    timestamp_zone: str = "UTC",
    generated_at: datetime | None = None,
) -> tuple[FastImportCommit, ...]:
    prepared = tuple(statutes)
    effective_dates = sorted({date_value for statute in prepared for date_value in statute.dates})
    zone = ZoneInfo(timestamp_zone)
    generated_datetime = _generated_datetime_for_zone(generated_at, zone)
    committer_timestamp, committer_offset = _raw_git_date_for_datetime(generated_datetime)
    commits: list[FastImportCommit] = []
    for effective_date in effective_dates:
        files: dict[str, bytes] = {}
        changed_statutes: list[tuple[PreparedStatute, MaterializedSnapshot]] = []
        for statute in prepared:
            snapshot = statute.snapshot_at_or_before(effective_date)
            if snapshot is None:
                continue
            if snapshot.effective_date == effective_date:
                changed_statutes.append((statute, snapshot))
            markdown = render_act_markdown(
                statute_id=statute.statute_id,
                title=statute.title,
                version_date=snapshot.effective_date,
                root=snapshot.root,
                tree_hash=snapshot.tree_hash,
                source_url=statute.source_url,
                interlink_rows=statute.interlink_rows,
                interlink_targets=statute.interlink_targets,
            )
            files[_statute_markdown_path(statute.statute_id, jurisdiction=jurisdiction)] = (
                _ensure_lf(markdown).encode("utf-8")
            )
        files["README.md"] = _ensure_lf(
            _render_readme(effective_date, prepared, jurisdiction=jurisdiction)
        ).encode("utf-8")
        author_timestamp, author_offset = _raw_git_date_for_effective_date(effective_date, zone)
        commits.append(
            FastImportCommit(
                effective_date=effective_date,
                message=_commit_message_for_date(
                    effective_date,
                    changed_statutes,
                ),
                files=dict(sorted(files.items())),
                timestamp=author_timestamp,
                timezone_offset=author_offset,
                committer_timestamp=committer_timestamp,
                committer_timezone_offset=committer_offset,
            )
        )
    return tuple(commits)


def build_fast_import_stream(commits: Iterable[FastImportCommit]) -> bytes:
    return b"".join(iter_fast_import_stream(commits))


def iter_fast_import_stream(commits: Iterable[FastImportCommit]) -> Iterator[bytes]:
    blob_mark = 1
    commit_mark = 1_000_000
    latest_commit_mark = 0
    for commit in commits:
        marks_by_path: dict[str, int] = {}
        for path, content in sorted(commit.files.items()):
            _validate_fast_import_path(path)
            mark = blob_mark
            blob_mark += 1
            marks_by_path[path] = mark
            yield b"blob\n"
            yield f"mark :{mark}\n".encode("ascii")
            yield _data_record(content)
        commit_mark += 1
        latest_commit_mark = commit_mark
        yield f"commit refs/heads/{_DEFAULT_BRANCH}\n".encode("ascii")
        yield f"mark :{commit_mark}\n".encode("ascii")
        author_date = f"{commit.timestamp} {commit.timezone_offset}"
        committer_timestamp = commit.committer_timestamp if commit.committer_timestamp is not None else commit.timestamp
        committer_date = f"{committer_timestamp} {commit.committer_timezone_offset}"
        yield f"author LawVM <lawvm@example.invalid> {author_date}\n".encode("ascii")
        yield f"committer LawVM <lawvm@example.invalid> {committer_date}\n".encode("ascii")
        yield _data_record(_ensure_lf(commit.message).encode("utf-8"))
        yield b"deleteall\n"
        for path, mark in marks_by_path.items():
            yield f"M 100644 :{mark} {path}\n".encode("utf-8")
        yield f"reset refs/tags/as-of/{commit.effective_date}\n".encode("ascii")
        yield f"from :{commit_mark}\n".encode("ascii")
    if latest_commit_mark:
        yield b"reset refs/tags/current\n"
        yield f"from :{latest_commit_mark}\n".encode("ascii")
    yield b"done\n"


def write_fast_import_stream(
    commits: Iterable[FastImportCommit],
    *,
    out_path: Path | None,
    repo_path: Path | None,
    force: bool,
) -> MarkdownGitExportStats:
    commit_tuple = tuple(commits)
    destination = "stdout"
    byte_count = 0
    if repo_path is not None:
        byte_count = _import_into_bare_repo(repo_path, commit_tuple, force=force)
        destination = str(repo_path)
    elif out_path is None:
        byte_count = _write_fast_import_chunks(sys.stdout.buffer, commit_tuple)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as fh:
            byte_count = _write_fast_import_chunks(fh, commit_tuple)
        destination = str(out_path)
    return MarkdownGitExportStats(
        statute_count=_statute_count_in_commits(commit_tuple),
        commit_count=len(commit_tuple),
        file_count=sum(len(commit.files) for commit in commit_tuple),
        byte_count=byte_count,
        destination=destination,
    )


def write_spooled_fast_import_stream(
    db_path: Path,
    *,
    jurisdiction: str,
    out_path: Path | None,
    repo_path: Path | None,
    force: bool,
    timestamp_zone: str = "UTC",
    generated_at: datetime | None = None,
) -> MarkdownGitExportStats:
    destination = "stdout"
    if repo_path is not None:
        byte_count = _import_spool_into_bare_repo(
            db_path,
            repo_path,
            jurisdiction=jurisdiction,
            force=force,
            timestamp_zone=timestamp_zone,
            generated_at=generated_at,
        )
        destination = str(repo_path)
    elif out_path is None:
        byte_count = _write_spooled_fast_import_chunks(
            sys.stdout.buffer,
            db_path,
            jurisdiction=jurisdiction,
            timestamp_zone=timestamp_zone,
            generated_at=generated_at,
        )
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as fh:
            byte_count = _write_spooled_fast_import_chunks(
                fh,
                db_path,
                jurisdiction=jurisdiction,
                timestamp_zone=timestamp_zone,
                generated_at=generated_at,
            )
        destination = str(out_path)
    return MarkdownGitExportStats(
        statute_count=_spool_statute_count(db_path),
        commit_count=_spool_commit_count(db_path),
        file_count=_spool_version_count(db_path) + _spool_commit_count(db_path),
        byte_count=byte_count,
        destination=destination,
    )


def iter_spooled_fast_import_stream(
    db_path: Path,
    *,
    jurisdiction: str,
    timestamp_zone: str = "UTC",
    generated_at: datetime | None = None,
) -> Iterator[bytes]:
    zone = ZoneInfo(timestamp_zone)
    generated_datetime = _generated_datetime_for_zone(generated_at, zone)
    committer_timestamp, committer_offset = _raw_git_date_for_datetime(generated_datetime)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    dates = _spool_effective_dates(conn)
    statutes = _spool_statutes(conn)
    blob_mark = 1
    commit_mark = 1_000_000
    latest_commit_mark = 0
    for effective_date in dates:
        blobs: list[tuple[str, bytes]] = [
            (
                "README.md",
                _ensure_lf(
                    _render_spool_readme(
                        effective_date,
                        statutes,
                        jurisdiction=jurisdiction,
                    )
                ).encode("utf-8"),
            )
        ]
        changed_versions = _spool_versions_for_date(conn, effective_date)
        blobs.extend((version.path, version.content) for version in changed_versions)
        marks_by_path: dict[str, int] = {}
        for path, content in sorted(blobs):
            _validate_fast_import_path(path)
            mark = blob_mark
            blob_mark += 1
            marks_by_path[path] = mark
            yield b"blob\n"
            yield f"mark :{mark}\n".encode("ascii")
            yield _data_record(content)
        commit_mark += 1
        yield f"commit refs/heads/{_DEFAULT_BRANCH}\n".encode("ascii")
        yield f"mark :{commit_mark}\n".encode("ascii")
        author_timestamp, author_offset = _raw_git_date_for_effective_date(effective_date, zone)
        yield f"author LawVM <lawvm@example.invalid> {author_timestamp} {author_offset}\n".encode(
            "ascii"
        )
        yield (
            f"committer LawVM <lawvm@example.invalid> "
            f"{committer_timestamp} {committer_offset}\n"
        ).encode("ascii")
        yield _data_record(
            _ensure_lf(
                _spool_commit_message_for_date(conn, effective_date, changed_versions)
            ).encode("utf-8")
        )
        if latest_commit_mark:
            yield f"from :{latest_commit_mark}\n".encode("ascii")
        latest_commit_mark = commit_mark
        for path, mark in marks_by_path.items():
            yield f"M 100644 :{mark} {path}\n".encode("utf-8")
        yield f"reset refs/tags/as-of/{effective_date}\n".encode("ascii")
        yield f"from :{commit_mark}\n".encode("ascii")
    if latest_commit_mark:
        yield b"reset refs/tags/current\n"
        yield f"from :{latest_commit_mark}\n".encode("ascii")
    yield b"done\n"
    conn.close()


def render_act_markdown(
    *,
    statute_id: str,
    title: str,
    version_date: str,
    root: IRNode,
    tree_hash: str,
    source_url: str = "",
    interlink_rows: Iterable[LawvmInterlinkRow] = (),
    interlink_targets: Iterable[LawvmInterlinkTargetRow] = (),
) -> str:
    links_by_segment = _links_by_segment(interlink_rows, version_date=version_date)
    target_by_key = {target.target_key: target for target in interlink_targets}
    lines: list[str] = [
        f"# {_escape_markdown(title or statute_id)}",
        "",
        f"- Statute: {_statute_label(statute_id, source_url)}",
        f"- Version: `{version_date}`",
        f"- LawVM tree hash: `{tree_hash}`",
        "",
    ]
    contents = _contents(root)
    if contents:
        lines.extend(["## Contents", ""])
        wrote_item = False
        for item in contents:
            if item.depth == 1 and wrote_item and lines[-1]:
                lines.append("")
            indent = "  " * (item.depth - 1)
            lines.append(f"{indent}- [{_escape_markdown(item.title)}](#{item.anchor})")
            wrote_item = True
        lines.append("")
    _render_node(
        root,
        (),
        lines,
        links_by_segment=links_by_segment,
        target_by_key=target_by_key,
    )
    return "\n".join(lines).rstrip() + "\n"


@dataclasses.dataclass(frozen=True, slots=True)
class _ContentsItem:
    anchor: str
    title: str
    depth: int


def _contents(root: IRNode) -> list[_ContentsItem]:
    items: list[_ContentsItem] = []

    def visit(node: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        counts: dict[str, int] = {}
        for child in node.children:
            kind = str(child.kind)
            if kind in {"part", "chapter", "section"} and child.label:
                counts[kind] = counts.get(kind, 0) + 1
                path = prefix + ((kind, _addr_component_for_node(child, counts[kind])),)
                items.append(
                    _ContentsItem(
                        anchor=_anchor_for_address(_node_address_string(path)),
                        title=_node_heading(child, fallback_label=child.label or ""),
                        depth=len(path),
                    )
                )
                visit(child, path)
            elif kind in _ADDRESSABLE_KINDS and child.label:
                counts[kind] = counts.get(kind, 0) + 1
                path = prefix + ((kind, _addr_component_for_node(child, counts[kind])),)
                visit(child, path)
            else:
                visit(child, prefix)

    visit(root, ())
    return items


def _render_node(
    node: IRNode,
    prefix: tuple[tuple[str, str], ...],
    lines: list[str],
    *,
    links_by_segment: Mapping[tuple[str, int], list[LawvmInterlinkRow]],
    target_by_key: Mapping[str, LawvmInterlinkTargetRow],
) -> None:
    counts: dict[str, int] = {}
    for child in node.children:
        kind = str(child.kind)
        if kind in _ADDRESSABLE_KINDS and child.label:
            counts[kind] = counts.get(kind, 0) + 1
            path = prefix + ((kind, _addr_component_for_node(child, counts[kind])),)
            address = _node_address_string(path)
            _render_addressable_node(
                child,
                path,
                address,
                lines,
                links_by_segment=links_by_segment,
                target_by_key=target_by_key,
            )
        elif kind not in {"num", "heading"}:
            _render_node(
                child,
                prefix,
                lines,
                links_by_segment=links_by_segment,
                target_by_key=target_by_key,
            )


def _render_addressable_node(
    node: IRNode,
    path: tuple[tuple[str, str], ...],
    address: str,
    lines: list[str],
    *,
    links_by_segment: Mapping[tuple[str, int], list[LawvmInterlinkRow]],
    target_by_key: Mapping[str, LawvmInterlinkTargetRow],
) -> None:
    kind = str(node.kind)
    anchor = _anchor_for_address(address)
    heading_level = _HEADING_LEVEL_BY_KIND.get(kind)
    if heading_level is not None:
        lines.append(f'<a id="{anchor}"></a>')
        lines.append(f"{'#' * heading_level} {_escape_markdown(_node_heading(node, fallback_label=node.label or ''))}")
        lines.append("")
    segments = _inline_segments_for_node(node)
    if kind == "subsection":
        lines.append(f'<a id="{anchor}"></a>')
        lines.append(f"#### {_escape_markdown(_subsection_heading(node.label or ''))}")
        lines.append("")
    rendered_segments: list[str] = []
    for segment_index, text in enumerate(segments):
        rendered = _render_segment(
            text,
            links_by_segment.get((address, segment_index), []),
            target_by_key=target_by_key,
        )
        rendered_segments.append(rendered)
        if kind in {"paragraph", "subparagraph"}:
            if segment_index == 0:
                lines.append(f'<a id="{anchor}"></a>')
            lines.append(_list_item_line(kind, node.label or "", rendered))
            lines.append("")
    if kind == "subsection" and rendered_segments:
        lines.extend(rendered_segments)
        lines.append("")
    elif kind not in _LOWER_ADDRESSABLE_KINDS:
        for rendered in rendered_segments:
            lines.append(rendered)
            lines.append("")
    _render_node(
        node,
        path,
        lines,
        links_by_segment=links_by_segment,
        target_by_key=target_by_key,
    )


def _render_segment(
    text: str,
    rows: Iterable[LawvmInterlinkRow],
    *,
    target_by_key: Mapping[str, LawvmInterlinkTargetRow],
) -> str:
    clean_rows: list[LawvmInterlinkRow] = []
    cursor = 0
    for row in sorted(
        rows,
        key=lambda item: (
            item.rendered_char_start if item.rendered_char_start is not None else -1,
            item.rendered_char_end if item.rendered_char_end is not None else -1,
        ),
    ):
        start = row.rendered_char_start
        end = row.rendered_char_end
        if start is None or end is None or start < cursor or end <= start or end > len(text):
            continue
        clean_rows.append(row)
        cursor = end
    rendered: list[str] = []
    pos = 0
    for row in clean_rows:
        start = int(row.rendered_char_start or 0)
        end = int(row.rendered_char_end or start)
        rendered.append(_escape_markdown(text[pos:start]))
        label = _escape_markdown(text[start:end])
        url = _row_markdown_url(row, target_by_key)
        if url:
            rendered.append(f"[{label}](<{url}>)")
        else:
            rendered.append(label)
        pos = end
    rendered.append(_escape_markdown(text[pos:]))
    return "".join(rendered)


def _row_markdown_url(
    row: LawvmInterlinkRow,
    target_by_key: Mapping[str, LawvmInterlinkTargetRow],
) -> str:
    if row.target_url:
        return row.target_url
    detail = json.loads(row.detail_json or "{}")
    target_key = str(detail.get("target_key") or "")
    target = target_by_key.get(target_key)
    if target is not None and target.target_url:
        return target.target_url
    return ""


def _links_by_segment(
    rows: Iterable[LawvmInterlinkRow],
    *,
    version_date: str,
) -> dict[tuple[str, int], list[LawvmInterlinkRow]]:
    out: dict[tuple[str, int], list[LawvmInterlinkRow]] = {}
    for row in rows:
        if row.rendered_effective_date != version_date:
            continue
        if row.rendered_address is None or row.rendered_segment_index is None:
            continue
        out.setdefault((row.rendered_address, row.rendered_segment_index), []).append(row)
    return out


def _inline_segments_for_node(node: IRNode) -> list[str]:
    segments: list[str] = []
    if node.text and node.text.strip():
        segments.append(node.text.strip())
    for child in node.children:
        kind = str(child.kind)
        if kind in _SKIP_INLINE_CHILD_KINDS:
            continue
        if child.text and child.text.strip():
            segments.append(child.text.strip())
    return segments


def _child_text(node: IRNode, kind: str) -> str:
    for child in node.children:
        if str(child.kind) == kind and child.text:
            return child.text.strip()
    return ""


def _node_heading(node: IRNode, *, fallback_label: str) -> str:
    num = _child_text(node, "num")
    heading = _child_text(node, "heading")
    if num and heading:
        return f"{num} {heading}"
    if heading:
        return heading
    if num:
        return num
    return fallback_label


def _subsection_heading(label: str) -> str:
    return f"({label})" if label else "Subsection"


def _list_item_line(kind: str, label: str, rendered: str) -> str:
    prefix = f"**{_escape_markdown(label)}.** " if label else ""
    if kind == "subparagraph":
        return f"  - {prefix}{rendered}"
    return f"- {prefix}{rendered}"


def _statute_label(statute_id: str, source_url: str) -> str:
    label = f"`{statute_id}`"
    if not source_url:
        return label
    return f"[{label}](<{source_url}>)"


def _commit_message_for_date(
    effective_date: str,
    changed_statutes: Iterable[tuple[PreparedStatute, MaterializedSnapshot]],
) -> str:
    changed = tuple(sorted(changed_statutes, key=lambda item: item[0].statute_id))
    lines = [f"As of {effective_date}"]
    if changed:
        lines.extend(["", "Changed statutes:"])
        for statute, _snapshot in changed:
            lines.append(f"- {_plain_statute_label(statute)}")
        cause_lines = _commit_cause_lines(changed, effective_date)
        if cause_lines:
            lines.extend(["", "Causes:", *cause_lines])
    return "\n".join(lines)


def _commit_cause_lines(
    changed_statutes: Iterable[tuple[PreparedStatute, MaterializedSnapshot]],
    effective_date: str,
) -> list[str]:
    lines: list[str] = []
    for statute, _snapshot in changed_statutes:
        causes = statute.amendment_causes_by_date.get(effective_date, ())
        if not causes:
            continue
        lines.append(f"- {_plain_statute_label(statute)}")
        for cause in causes:
            lines.append(f"  - {cause.kind}: {_plain_amendment_cause_label(cause)}")
    return lines


def _plain_statute_label(statute: PreparedStatute) -> str:
    title = statute.title.strip()
    return f"{statute.statute_id} {title}" if title else statute.statute_id


def _plain_amendment_cause_label(cause: AmendmentCause) -> str:
    title = cause.title.strip()
    return f"{cause.source_id} {title}" if title else cause.source_id


def _addr_component_for_node(node: IRNode, ordinal: int) -> str:
    label = str(node.label or "").strip()
    if label:
        return re.sub(r"\s+", "", label)
    num = _child_text(node, "num")
    if num:
        cleaned = re.sub(r"[\u00a7).]", "", num)
        cleaned = re.sub(r"\s+", "", cleaned.strip())
        if cleaned:
            return cleaned
    return str(ordinal)


def _node_address_string(path: tuple[tuple[str, str], ...]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _anchor_for_address(address: str) -> str:
    lowered = address.lower().replace(":", "-").replace("/", "-")
    cleaned = _ANCHOR_CLEANUP_RE.sub("-", lowered).strip("-")
    return cleaned or "act"


def _escape_markdown(text: str) -> str:
    return _MARKDOWN_ESCAPE_RE.sub(r"\\\1", text)


def _statute_markdown_path(statute_id: str, *, jurisdiction: str) -> str:
    num, sep, year = statute_id.partition("/")
    if sep and re.fullmatch(r"\d{4}", year):
        return f"acts/{_safe_path_component(year)}/{_safe_path_component(num)}.md"
    parts = [_safe_path_component(part) for part in statute_id.split("/") if part.strip()]
    filename = "__".join(parts) if parts else "unknown"
    return f"acts/{_safe_path_component(jurisdiction)}/{filename}.md"


def _safe_path_component(value: str) -> str:
    cleaned = _SAFE_PATH_RE.sub("-", value.strip()).strip("-")
    return cleaned or "unknown"


def _render_readme(
    effective_date: str,
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} LawVM Markdown Projection",
        "",
        f"- Repository snapshot date: `{effective_date}`",
        "",
        "## Statutes",
        "",
    ]
    for statute in sorted(
        statutes,
        key=lambda item: _statute_markdown_path(item.statute_id, jurisdiction=jurisdiction),
    ):
        lines.append(
            f"- [{_escape_markdown(statute.title)}]("
            f"{_statute_markdown_path(statute.statute_id, jurisdiction=jurisdiction)}) "
            f"`{statute.statute_id}`"
        )
    return "\n".join(lines)


def _render_spool_readme(
    effective_date: str,
    statutes: tuple[SpoolStatuteRow, ...],
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} LawVM Markdown Projection",
        "",
        f"- Repository snapshot date: `{effective_date}`",
        "",
        "## Statutes",
        "",
    ]
    for statute in sorted(statutes, key=lambda item: item.path):
        lines.append(
            f"- [{_escape_markdown(statute.title)}]({statute.path}) "
            f"`{statute.statute_id}`"
        )
    return "\n".join(lines)


def _data_record(payload: bytes) -> bytes:
    return f"data {len(payload)}\n".encode("ascii") + payload


def _ensure_lf(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _raw_git_date_for_effective_date(effective_date: str, zone: ZoneInfo) -> tuple[int, str]:
    parsed = datetime.strptime(effective_date, "%Y-%m-%d").replace(
        hour=12,
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=zone,
    )
    return _raw_git_date_for_datetime(parsed)


def _generated_datetime_for_zone(value: datetime | None, zone: ZoneInfo) -> datetime:
    if value is None:
        return datetime.now(zone)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _raw_git_date_for_datetime(value: datetime) -> tuple[int, str]:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    timestamp = max(0, int(value.timestamp()))
    offset = value.utcoffset() or timezone.utc.utcoffset(value)
    offset_seconds = int(offset.total_seconds()) if offset is not None else 0
    return timestamp, _git_timezone_offset(offset_seconds)


def _git_timezone_offset(offset_seconds: int) -> str:
    sign = "+" if offset_seconds >= 0 else "-"
    absolute_minutes = abs(offset_seconds) // 60
    hours, minutes = divmod(absolute_minutes, 60)
    return f"{sign}{hours:02d}{minutes:02d}"


def _validate_fast_import_path(path: str) -> None:
    if not path or path.startswith("/") or "\n" in path or "\r" in path:
        raise ValueError(f"invalid fast-import path: {path!r}")
    if path == "." or ".." in path.split("/"):
        raise ValueError(f"invalid fast-import path: {path!r}")


def _write_fast_import_chunks(sink: BinaryIO, commits: Iterable[FastImportCommit]) -> int:
    byte_count = 0
    for chunk in iter_fast_import_stream(commits):
        sink.write(chunk)
        byte_count += len(chunk)
    return byte_count


def _import_into_bare_repo(
    repo_path: Path,
    commits: Iterable[FastImportCommit],
    *,
    force: bool,
) -> int:
    if repo_path.exists():
        if not force:
            raise FileExistsError(f"repository path already exists: {repo_path}")
        shutil.rmtree(repo_path)
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(repo_path), "symbolic-ref", "HEAD", f"refs/heads/{_DEFAULT_BRANCH}"],
        check=True,
    )
    proc = subprocess.Popen(
        ["git", "--git-dir", str(repo_path), "fast-import", "--date-format=raw"],
        stdin=subprocess.PIPE,
    )
    if proc.stdin is None:
        raise RuntimeError("git fast-import stdin was not opened")
    byte_count = _write_fast_import_chunks(proc.stdin, commits)
    proc.stdin.close()
    return_code = proc.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, proc.args)
    return byte_count


def _write_spooled_fast_import_chunks(
    sink: BinaryIO,
    db_path: Path,
    *,
    jurisdiction: str,
    timestamp_zone: str,
    generated_at: datetime | None,
) -> int:
    byte_count = 0
    for chunk in iter_spooled_fast_import_stream(
        db_path,
        jurisdiction=jurisdiction,
        timestamp_zone=timestamp_zone,
        generated_at=generated_at,
    ):
        sink.write(chunk)
        byte_count += len(chunk)
    return byte_count


def _import_spool_into_bare_repo(
    db_path: Path,
    repo_path: Path,
    *,
    jurisdiction: str,
    force: bool,
    timestamp_zone: str,
    generated_at: datetime | None,
) -> int:
    if repo_path.exists():
        if not force:
            raise FileExistsError(f"repository path already exists: {repo_path}")
        shutil.rmtree(repo_path)
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(repo_path)], check=True)
    subprocess.run(
        ["git", "--git-dir", str(repo_path), "symbolic-ref", "HEAD", f"refs/heads/{_DEFAULT_BRANCH}"],
        check=True,
    )
    proc = subprocess.Popen(
        ["git", "--git-dir", str(repo_path), "fast-import", "--date-format=raw"],
        stdin=subprocess.PIPE,
    )
    if proc.stdin is None:
        raise RuntimeError("git fast-import stdin was not opened")
    byte_count = _write_spooled_fast_import_chunks(
        proc.stdin,
        db_path,
        jurisdiction=jurisdiction,
        timestamp_zone=timestamp_zone,
        generated_at=generated_at,
    )
    proc.stdin.close()
    return_code = proc.wait()
    if return_code:
        raise subprocess.CalledProcessError(return_code, proc.args)
    return byte_count


def _spool_statute_count(db_path: Path) -> int:
    return _spool_count(db_path, "SELECT COUNT(*) FROM statutes")


def _spool_commit_count(db_path: Path) -> int:
    return _spool_count(db_path, "SELECT COUNT(DISTINCT effective_date) FROM versions")


def _spool_version_count(db_path: Path) -> int:
    return _spool_count(db_path, "SELECT COUNT(*) FROM versions")


def _spool_count(db_path: Path, sql: str) -> int:
    conn = sqlite3.connect(str(db_path))
    value = conn.execute(sql).fetchone()[0]
    conn.close()
    return int(value)


def _spool_effective_dates(conn: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row["effective_date"])
        for row in conn.execute("SELECT DISTINCT effective_date FROM versions ORDER BY effective_date")
    )


def _spool_statutes(conn: sqlite3.Connection) -> tuple[SpoolStatuteRow, ...]:
    return tuple(
        SpoolStatuteRow(
            statute_id=str(row["statute_id"]),
            title=str(row["title"]),
            path=str(row["path"]),
        )
        for row in conn.execute("SELECT statute_id, title, path FROM statutes ORDER BY statute_id")
    )


def _spool_versions_for_date(conn: sqlite3.Connection, effective_date: str) -> tuple[SpoolVersionRow, ...]:
    return tuple(
        SpoolVersionRow(
            statute_id=str(row["statute_id"]),
            title=str(row["title"]),
            path=str(row["path"]),
            effective_date=str(row["effective_date"]),
            content=bytes(row["content"]),
        )
        for row in conn.execute(
            """
            SELECT v.statute_id, s.title, v.path, v.effective_date, v.content
            FROM versions v
            JOIN statutes s ON s.statute_id = v.statute_id
            WHERE v.effective_date = ?
            ORDER BY v.path
            """,
            (effective_date,),
        )
    )


def _spool_commit_message_for_date(
    conn: sqlite3.Connection,
    effective_date: str,
    versions: Iterable[SpoolVersionRow],
) -> str:
    changes = tuple(
        SpoolStatuteChange(
            statute_id=version.statute_id,
            title=version.title,
            causes=_spool_causes_for_statute(conn, effective_date, version.statute_id),
        )
        for version in sorted(versions, key=lambda item: item.statute_id)
    )
    lines = [f"As of {effective_date}"]
    if changes:
        lines.extend(["", "Changed statutes:"])
        for change in changes:
            lines.append(f"- {_plain_spool_statute_label(change)}")
        cause_lines = _spool_commit_cause_lines(changes)
        if cause_lines:
            lines.extend(["", "Causes:", *cause_lines])
    return "\n".join(lines)


def _spool_causes_for_statute(
    conn: sqlite3.Connection,
    effective_date: str,
    statute_id: str,
) -> tuple[AmendmentCause, ...]:
    return tuple(
        AmendmentCause(
            source_id=str(row["source_id"]),
            title=str(row["title"]),
            source_url=str(row["source_url"]),
            kind=str(row["kind"]),
        )
        for row in conn.execute(
            """
            SELECT kind, source_id, title, source_url
            FROM causes
            WHERE effective_date = ? AND statute_id = ?
            ORDER BY kind, source_id
            """,
            (effective_date, statute_id),
        )
    )


def _spool_commit_cause_lines(changes: Iterable[SpoolStatuteChange]) -> list[str]:
    lines: list[str] = []
    for change in changes:
        if not change.causes:
            continue
        lines.append(f"- {_plain_spool_statute_label(change)}")
        for cause in change.causes:
            lines.append(f"  - {cause.kind}: {_plain_amendment_cause_label(cause)}")
    return lines


def _plain_spool_statute_label(statute: SpoolStatuteChange) -> str:
    title = statute.title.strip()
    return f"{statute.statute_id} {title}" if title else statute.statute_id


def _statute_count_in_commits(commits: tuple[FastImportCommit, ...]) -> int:
    paths: set[str] = set()
    for commit in commits:
        for path in commit.files:
            if path.startswith("acts/") and path.endswith(".md"):
                paths.add(path)
    return len(paths)


def _parse_output_path(value: str | None) -> Path | None:
    if value is None or value == "-":
        return None
    return Path(value)


def export_markdown_git_with_spool(
    statute_ids: Iterable[str],
    *,
    jurisdiction: str,
    db_path: Path,
    include_future: bool,
    until: str | None,
    workers: int,
    out_path: Path | None,
    repo_path: Path | None,
    force: bool,
    timestamp_zone: str,
) -> MarkdownGitExportStats:
    build_markdown_git_spool(
        statute_ids,
        jurisdiction=jurisdiction,
        db_path=db_path,
        include_future=include_future,
        until=until,
        workers=workers,
    )
    return write_spooled_fast_import_stream(
        db_path,
        jurisdiction=jurisdiction,
        out_path=out_path,
        repo_path=repo_path,
        force=force,
        timestamp_zone=timestamp_zone,
    )


def main(args: argparse.Namespace) -> None:
    if getattr(args, "repo", None) and getattr(args, "out", "-") not in ("-", None):
        raise SystemExit("--repo cannot be combined with --out; pipe stdout or use --repo")
    jurisdiction = str(getattr(args, "jurisdiction", "fi") or "fi")
    statute_ids = tuple(getattr(args, "statute", None) or ())
    if not statute_ids:
        statute_ids = statute_ids_from_manifest(Path(args.manifest), jurisdiction=jurisdiction)
    if not statute_ids:
        raise SystemExit(f"no {jurisdiction} statutes selected")
    workers = int(getattr(args, "workers", 1) or 1)
    if workers < 1:
        raise SystemExit("--workers must be >= 1")
    include_future = bool(getattr(args, "include_future", True))
    until = getattr(args, "until", None)
    timestamp_zone = str(getattr(args, "timestamp_zone", "UTC") or "UTC")
    out_path = _parse_output_path(getattr(args, "out", "-"))
    repo_path = Path(args.repo) if getattr(args, "repo", None) else None
    force = bool(getattr(args, "force", False))
    spool_db = getattr(args, "spool_db", None)
    if workers > 1 or spool_db:
        if spool_db:
            stats = export_markdown_git_with_spool(
                statute_ids,
                jurisdiction=jurisdiction,
                db_path=Path(spool_db),
                include_future=include_future,
                until=until,
                workers=workers,
                out_path=out_path,
                repo_path=repo_path,
                force=force,
                timestamp_zone=timestamp_zone,
            )
        else:
            with TemporaryDirectory(prefix="lawvm-markdown-git-") as tmpdir:
                stats = export_markdown_git_with_spool(
                    statute_ids,
                    jurisdiction=jurisdiction,
                    db_path=Path(tmpdir) / "spool.db",
                    include_future=include_future,
                    until=until,
                    workers=workers,
                    out_path=out_path,
                    repo_path=repo_path,
                    force=force,
                    timestamp_zone=timestamp_zone,
                )
    else:
        prepared = prepare_markdown_git_export(
            statute_ids,
            jurisdiction=jurisdiction,
            include_future=include_future,
            until=until,
        )
        commits = build_markdown_git_commits(
            prepared,
            jurisdiction=jurisdiction,
            timestamp_zone=timestamp_zone,
        )
        stats = write_fast_import_stream(
            commits,
            out_path=out_path,
            repo_path=repo_path,
            force=force,
        )
    print(
        "export-markdown-git: "
        f"statutes={stats.statute_count} commits={stats.commit_count} "
        f"files={stats.file_count} bytes={stats.byte_count} dest={stats.destination}",
        file=sys.stderr,
    )
