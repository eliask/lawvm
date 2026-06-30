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
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO, cast
from zoneinfo import ZoneInfo

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import is_zombie, structural_subtree_hash
from lawvm.tools.export_transition_graph import ReplayBundle
from lawvm.tools.export_transition_graph import covering_units
from lawvm.tools.transition_graph_interlinks import (
    InterlinkTargetPreviewContext,
    LawvmInterlinkRow,
    LawvmInterlinkTargetRow,
    enrich_lawvm_interlink_targets,
    place_lawvm_interlinks,
    rendered_text_segments,
)
from lawvm.corpus_store import CorpusStore
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
# Path-component sanitizer: allows [A-Za-z0-9._-] only. `/` is intentionally
# excluded so a caller that forgets to split on `/` cannot smuggle a directory
# separator (or a `..` traversal via `/`) through this function. The contract:
# callers must split on `/` first (see `_statute_markdown_path`); this sanitizer
# does NOT preserve directory separators.
_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9._-]+")
_NUMERIC_STATUTE_ID_RE = re.compile(
    r"^(?P<number>[0-9]{1,6}|[0-9]{1,6}-[A-Za-z0-9]{1,8})/(?P<year>[0-9]{4})$"
)
_NUMERIC_STATUTE_PATH_RE = re.compile(
    r"^acts/(?P<year>[0-9]{4})/(?P<number>[0-9]{1,6}|[0-9]{1,6}-[A-Za-z0-9]{1,8})\.md$"
)
_ROOT_README_YEAR_LINKS_PER_ROW = 8
_DEFAULT_BRANCH = "in-force"
_SUBSTANTIVE_BODY_KINDS = frozenset(
    {
        "article",
        "chapter",
        "clause",
        "mainBody",
        "paragraph",
        "part",
        "p",
        "section",
        "subchapter",
        "subparagraph",
        "subsection",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class MaterializedSnapshot:
    effective_date: str
    root: IRNode
    tree_hash: str
    in_force: bool = True


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

    def in_force_snapshot_at_or_before(self, as_of: str) -> MaterializedSnapshot | None:
        snapshot = self.snapshot_at_or_before(as_of)
        if snapshot is None or not snapshot.in_force:
            return None
        return snapshot


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
    skipped_count: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class RenderedMarkdownVersion:
    effective_date: str
    tree_hash: str
    content: bytes
    in_force: bool = True


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
class SkippedMarkdownStatute:
    statute_id: str
    reason_kind: str
    message: str


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitRenderRequest:
    statute_id: str
    jurisdiction: str
    include_future: bool
    until: str | None
    skip_failures: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitSpoolBuildStats:
    statute_count: int
    version_count: int
    byte_count: int
    spool_path: Path
    skipped_count: int = 0


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
    in_force: bool


@dataclasses.dataclass(frozen=True, slots=True)
class SpoolStatuteChange:
    statute_id: str
    title: str
    causes: tuple[AmendmentCause, ...]


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


def statute_ids_from_fi_corpus(*, substantive_body_only: bool = True) -> tuple[str, ...]:
    adapter = transition_graph_adapter_for_jurisdiction("fi")
    corpus_obj = adapter.profile.corpus()
    if corpus_obj is None:
        raise ValueError("Finland transition-graph profile has no corpus")
    corpus = cast(CorpusStore, corpus_obj)
    raw_statute_ids = _fi_base_law_ids_from_corpus(corpus)
    accepted_ids: list[str] = []
    for raw_id in raw_statute_ids:
        if substantive_body_only and not _corpus_base_law_has_substantive_body(corpus, raw_id):
            continue
        accepted_ids.append(adapter.profile.canonical_statute_id(raw_id))
    return tuple(
        sorted(
            set(accepted_ids),
            key=lambda item: _statute_list_sort_key_for_id(item, jurisdiction="fi"),
        )
    )


def _fi_base_law_ids_from_corpus(corpus: CorpusStore) -> tuple[str, ...]:
    oracle = corpus.oracle_path_index()
    if oracle:
        return tuple(str(statute_id) for statute_id in oracle)
    statute_ids = corpus.list_statute_ids()
    if statute_ids:
        return tuple(str(statute_id) for statute_id in statute_ids)
    raise ValueError("Finland corpus exposes neither oracle_path_index nor list_statute_ids")


def _corpus_base_law_has_substantive_body(corpus: CorpusStore, statute_id: str) -> bool:
    xml_bytes = corpus.read_oracle(statute_id)
    if xml_bytes:
        return _source_xml_has_substantive_body(xml_bytes)
    return _corpus_source_has_substantive_body(corpus, statute_id)


def _corpus_source_has_substantive_body(corpus: CorpusStore, statute_id: str) -> bool:
    xml_bytes = corpus.read_source(statute_id)
    if not xml_bytes:
        return False
    return _source_xml_has_substantive_body(xml_bytes)


def _source_xml_has_substantive_body(xml_bytes: bytes) -> bool:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return False
    body = root.find(".//{*}body")
    if body is None:
        body = root.find(".//{*}mainBody")
    if body is None:
        return False
    for element in body.iter():
        if element is body:
            continue
        if _element_local_name(element.tag) in _SUBSTANTIVE_BODY_KINDS and _element_text(element):
            return True
    return bool(_element_text(body))


def _element_local_name(tag: str) -> str:
    _namespace, _sep, local_name = tag.rpartition("}")
    return local_name or tag


def _element_text(element: ET.Element[str]) -> str:
    return " ".join(text.strip() for text in element.itertext() if text and text.strip())


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
                    in_force=_materialized_root_in_force(root, effective_date),
                )
            )
        if not snapshots:
            continue
        if not any(snapshot.in_force for snapshot in snapshots):
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


def render_markdown_git_statute(
    request: MarkdownGitRenderRequest,
) -> RenderedMarkdownStatute | SkippedMarkdownStatute | None:
    try:
        prepared = prepare_markdown_git_export(
            (request.statute_id,),
            jurisdiction=request.jurisdiction,
            include_future=request.include_future,
            until=request.until,
        )
    except Exception as exc:
        if request.skip_failures:
            return SkippedMarkdownStatute(
                statute_id=request.statute_id,
                reason_kind=exc.__class__.__name__,
                message=str(exc),
            )
        raise
    if not prepared:
        return SkippedMarkdownStatute(
            statute_id=request.statute_id,
            reason_kind="no_materialized_snapshots",
            message="replay produced no exportable snapshots",
        )
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
                in_force=snapshot.in_force,
            )
        )
    if not any(version.in_force for version in versions):
        return SkippedMarkdownStatute(
            statute_id=request.statute_id,
            reason_kind="no_in_force_snapshots",
            message="replay produced no in-force statute versions",
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


def _materialized_root_in_force(root: IRNode, effective_date: str) -> bool:
    for _address, node in covering_units(root, granularity="subsection"):
        if _addressable_node_in_force(node, effective_date):
            return True
    return False


def _addressable_node_in_force(node: IRNode, effective_date: str) -> bool:
    attrs = node.attrs
    if (
        attrs.get("lawvm_repeal_placeholder") == "1"
        or attrs.get("lawvm_tombstone")
        or attrs.get("content_state") in {"tombstone", "scaffold"}
    ):
        return False
    return not is_zombie(node, effective_date)


def build_markdown_git_spool(
    statute_ids: Iterable[str],
    *,
    jurisdiction: str,
    db_path: Path,
    include_future: bool = True,
    until: str | None = None,
    workers: int = 1,
    skip_failures: bool = False,
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
            skip_failures=skip_failures,
        )
        for statute_id in ids
    )
    skipped_count = 0
    for rendered in _iter_rendered_markdown_statutes(requests, workers=workers):
        if rendered is None:
            continue
        if isinstance(rendered, SkippedMarkdownStatute):
            with conn:
                _insert_skipped_statute(conn, rendered)
            skipped_count += 1
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
        skipped_count=skipped_count,
    )


def _iter_rendered_markdown_statutes(
    requests: tuple[MarkdownGitRenderRequest, ...],
    *,
    workers: int,
) -> Iterator[RenderedMarkdownStatute | SkippedMarkdownStatute | None]:
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
          in_force INTEGER NOT NULL,
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

        CREATE TABLE skipped_statutes (
          statute_id TEXT PRIMARY KEY,
          reason_kind TEXT NOT NULL,
          message TEXT NOT NULL
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
        INSERT OR REPLACE INTO versions(statute_id, effective_date, tree_hash, path, content, in_force)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            (
                statute.statute_id,
                version.effective_date,
                version.tree_hash,
                statute.path,
                version.content,
                1 if version.in_force else 0,
            )
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


def _insert_skipped_statute(conn: sqlite3.Connection, skipped: SkippedMarkdownStatute) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO skipped_statutes(statute_id, reason_kind, message)
        VALUES (?, ?, ?)
        """,
        (skipped.statute_id, skipped.reason_kind, skipped.message),
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
        active_statutes: list[PreparedStatute] = []
        for statute in prepared:
            snapshot = statute.snapshot_at_or_before(effective_date)
            if snapshot is None:
                continue
            if snapshot.effective_date == effective_date:
                changed_statutes.append((statute, snapshot))
            if not snapshot.in_force:
                continue
            active_statutes.append(statute)
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
        active_tuple = tuple(active_statutes)
        files["README.md"] = _ensure_lf(
            _render_readme(active_tuple, jurisdiction=jurisdiction)
        ).encode("utf-8")
        files.update(_render_year_readme_files(active_tuple, jurisdiction=jurisdiction))
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
    commit_count = _spool_commit_count(db_path)
    file_count = 0
    if commit_count:
        file_count = _spool_version_count(db_path) + _spool_index_write_count(db_path)
    return MarkdownGitExportStats(
        statute_count=_spool_statute_count(db_path),
        commit_count=commit_count,
        file_count=file_count,
        byte_count=byte_count,
        destination=destination,
        skipped_count=_spool_skipped_count(db_path),
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
    blob_mark = 1
    commit_mark = 1_000_000
    latest_commit_mark = 0
    previous_index_paths: set[str] = set()
    previous_statute_paths: set[str] = set()
    for effective_date in dates:
        blobs: list[tuple[str, bytes]] = []
        changed_versions = _spool_versions_for_date(conn, effective_date)
        active_statutes = _spool_active_statutes_at(conn, effective_date)
        index_blobs = _spool_index_blobs(active_statutes, jurisdiction=jurisdiction)
        current_index_paths = {path for path, _content in index_blobs}
        current_statute_paths = {statute.path for statute in active_statutes}
        delete_paths = {
            version.path
            for version in changed_versions
            if not version.in_force and version.path in previous_statute_paths
        }
        delete_paths.update(previous_index_paths - current_index_paths)
        blobs.extend(index_blobs)
        blobs.extend((version.path, version.content) for version in changed_versions if version.in_force)
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
        for path in sorted(delete_paths - set(marks_by_path)):
            _validate_fast_import_path(path)
            yield f"D {path}\n".encode("utf-8")
        for path, mark in marks_by_path.items():
            yield f"M 100644 :{mark} {path}\n".encode("utf-8")
        previous_index_paths = current_index_paths
        previous_statute_paths = current_statute_paths
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
    # Sanitize a single path component (filename or directory leaf). Strips
    # `/` (and any other char outside [A-Za-z0-9._-]) to `-`. Contract: callers
    # must split on `/` first; this sanitizer does NOT preserve directory
    # separators and will collapse a `../`-style traversal into a flat token.
    #
    # Trim leading/trailing `.` as well as `-` (iter2 W6 LOW/M-batch Fix 3):
    # the substring `..` survives the regex sub because `.` is allowed, so a
    # bare `..` component would otherwise pop out as the literal `..`. Even
    # though the caller's `__`-join pattern means `..` is valueless as a
    # traversal vector, leaving it as a path-component leaf is unnecessary
    # and reads as unsafe to a security reviewer scanning the output. A
    # leading/trailing-dot strip turns `..`/`.`/`-..-` into the `unknown`
    # placeholder (since the result is empty) without disturbing interior
    # dots in legitimate identifiers like `a.b.c-1`.
    cleaned = _SAFE_PATH_RE.sub("-", value.strip()).strip("-.")
    return cleaned or "unknown"


def _render_readme(
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} LawVM Markdown Projection",
        "",
        "## Years",
        "",
    ]
    lines.extend(_root_readme_year_rows(_years_for_prepared_statutes(statutes, jurisdiction=jurisdiction)))
    other = _other_prepared_statutes(statutes, jurisdiction=jurisdiction)
    if other:
        lines.extend(["", "## Other Statutes", ""])
        for statute in other:
            path = _statute_markdown_path(statute.statute_id, jurisdiction=jurisdiction)
            lines.append(f"- [{_escape_markdown(statute.title)}]({path}) `{statute.statute_id}`")
    return "\n".join(lines)


def _render_year_readme_files(
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for year in _years_for_prepared_statutes(statutes, jurisdiction=jurisdiction):
        rows = tuple(
            statute
            for statute in statutes
            if _year_for_statute_id(statute.statute_id, jurisdiction=jurisdiction) == year
        )
        files[f"acts/{year}/README.md"] = _ensure_lf(
            _render_year_readme(
                year,
                rows,
                jurisdiction=jurisdiction,
            )
        ).encode("utf-8")
    return files


def _render_year_readme(
    year: str,
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} {year} Statutes",
        "",
        "## Statutes",
        "",
    ]
    for statute in sorted(
        statutes,
        key=lambda item: _statute_list_sort_key_for_id(item.statute_id, jurisdiction=jurisdiction),
    ):
        path = Path(_statute_markdown_path(statute.statute_id, jurisdiction=jurisdiction)).name
        lines.append(f"- [{_escape_markdown(statute.title)}]({path}) `{statute.statute_id}`")
    return "\n".join(lines)


def _render_spool_readme(
    statutes: tuple[SpoolStatuteRow, ...],
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} LawVM Markdown Projection",
        "",
        "## Years",
        "",
    ]
    lines.extend(_root_readme_year_rows(_years_for_spool_statutes(statutes)))
    other = _other_spool_statutes(statutes)
    if other:
        lines.extend(["", "## Other Statutes", ""])
        for statute in other:
            lines.append(f"- [{_escape_markdown(statute.title)}]({statute.path}) `{statute.statute_id}`")
    return "\n".join(lines)


def _spool_year_readme_blobs(statutes: tuple[SpoolStatuteRow, ...]) -> list[tuple[str, bytes]]:
    blobs: list[tuple[str, bytes]] = []
    for year in _years_for_spool_statutes(statutes):
        rows = tuple(statute for statute in statutes if _year_for_statute_path(statute.path) == year)
        blobs.append(
            (
                f"acts/{year}/README.md",
                _ensure_lf(_render_spool_year_readme(year, rows)).encode("utf-8"),
            )
        )
    return blobs


def _render_spool_year_readme(
    year: str,
    statutes: tuple[SpoolStatuteRow, ...],
) -> str:
    lines = [
        f"# {year} Statutes",
        "",
        "## Statutes",
        "",
    ]
    for statute in sorted(statutes, key=lambda item: _statute_list_sort_key_for_path(item.path)):
        filename = Path(statute.path).name
        lines.append(f"- [{_escape_markdown(statute.title)}]({filename}) `{statute.statute_id}`")
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


def _statute_list_sort_key_for_id(statute_id: str, *, jurisdiction: str) -> tuple[str, str, str]:
    match = _NUMERIC_STATUTE_ID_RE.fullmatch(statute_id.strip())
    if match is not None:
        number = match.group("number")
        year = match.group("year")
        return (year, _numeric_statute_number_sort_token(number), statute_id)
    path = _statute_markdown_path(statute_id, jurisdiction=jurisdiction)
    return _statute_list_sort_key_for_path(path)


def _statute_list_sort_key_for_path(path: str) -> tuple[str, str, str]:
    match = _NUMERIC_STATUTE_PATH_RE.fullmatch(path.strip())
    if match is not None:
        year = match.group("year")
        number = match.group("number")
        return (year, _numeric_statute_number_sort_token(number), path)
    return ("zzzz", path, "")


def _numeric_statute_number_sort_token(number: str) -> str:
    main, sep, suffix = number.partition("-")
    main_token = f"{int(main):06d}" if main.isdigit() else main
    if not sep:
        return main_token
    suffix_token = f"{int(suffix):06d}" if suffix.isdigit() else suffix.lower()
    return f"{main_token}-{suffix_token}"


def _root_readme_year_rows(years: Iterable[str]) -> list[str]:
    sorted_years = tuple(sorted(years))
    if not sorted_years:
        return []
    lines: list[str] = []
    for start in range(0, len(sorted_years), _ROOT_README_YEAR_LINKS_PER_ROW):
        chunk = sorted_years[start : start + _ROOT_README_YEAR_LINKS_PER_ROW]
        links = " | ".join(f"[{year}](acts/{year}/)" for year in chunk)
        lines.append(f"- {links}")
    return lines


def _year_for_statute_id(statute_id: str, *, jurisdiction: str) -> str | None:
    match = _NUMERIC_STATUTE_ID_RE.fullmatch(statute_id.strip())
    if match is not None:
        return match.group("year")
    return _year_for_statute_path(_statute_markdown_path(statute_id, jurisdiction=jurisdiction))


def _year_for_statute_path(path: str) -> str | None:
    match = _NUMERIC_STATUTE_PATH_RE.fullmatch(path.strip())
    if match is None:
        return None
    return match.group("year")


def _years_for_prepared_statutes(
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> tuple[str, ...]:
    years = {
        year
        for statute in statutes
        if (year := _year_for_statute_id(statute.statute_id, jurisdiction=jurisdiction)) is not None
    }
    return tuple(sorted(years))


def _other_prepared_statutes(
    statutes: tuple[PreparedStatute, ...],
    *,
    jurisdiction: str,
) -> tuple[PreparedStatute, ...]:
    return tuple(
        sorted(
            (
                statute
                for statute in statutes
                if _year_for_statute_id(statute.statute_id, jurisdiction=jurisdiction) is None
            ),
            key=lambda item: _statute_markdown_path(item.statute_id, jurisdiction=jurisdiction),
        )
    )


def _years_for_spool_statutes(statutes: tuple[SpoolStatuteRow, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {year for statute in statutes if (year := _year_for_statute_path(statute.path)) is not None}
        )
    )


def _other_spool_statutes(statutes: tuple[SpoolStatuteRow, ...]) -> tuple[SpoolStatuteRow, ...]:
    return tuple(
        sorted(
            (statute for statute in statutes if _year_for_statute_path(statute.path) is None),
            key=lambda item: item.path,
        )
    )


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
    byte_count = _write_fast_import_chunks(cast(BinaryIO, proc.stdin), commits)
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
        cast(BinaryIO, proc.stdin),
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


def _spool_year_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        years = {
            year
            for (path,) in conn.execute("SELECT path FROM statutes")
            if (year := _year_for_statute_path(str(path))) is not None
        }
    finally:
        conn.close()
    return len(years)


def _spool_index_write_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        count = 0
        for effective_date in _spool_effective_dates(conn):
            active = _spool_active_statutes_at(conn, effective_date)
            count += 1 + len(_years_for_spool_statutes(active))
        return count
    finally:
        conn.close()


def _spool_skipped_count(db_path: Path) -> int:
    return _spool_count(db_path, "SELECT COUNT(*) FROM skipped_statutes")


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
            in_force=bool(row["in_force"]),
        )
        for row in conn.execute(
            """
            SELECT v.statute_id, s.title, v.path, v.effective_date, v.content, v.in_force
            FROM versions v
            JOIN statutes s ON s.statute_id = v.statute_id
            WHERE v.effective_date = ?
            ORDER BY v.path
            """,
            (effective_date,),
        )
    )


def _spool_active_statutes_at(
    conn: sqlite3.Connection,
    effective_date: str,
) -> tuple[SpoolStatuteRow, ...]:
    return tuple(
        SpoolStatuteRow(
            statute_id=str(row["statute_id"]),
            title=str(row["title"]),
            path=str(row["path"]),
        )
        for row in conn.execute(
            """
            SELECT s.statute_id, s.title, s.path
            FROM statutes s
            JOIN versions v ON v.statute_id = s.statute_id
            WHERE v.effective_date = (
              SELECT MAX(v2.effective_date)
              FROM versions v2
              WHERE v2.statute_id = s.statute_id AND v2.effective_date <= ?
            )
            AND v.in_force = 1
            ORDER BY s.path
            """,
            (effective_date,),
        )
    )


def _spool_index_blobs(
    statutes: tuple[SpoolStatuteRow, ...],
    *,
    jurisdiction: str,
) -> list[tuple[str, bytes]]:
    return [
        (
            "README.md",
            _ensure_lf(_render_spool_readme(statutes, jurisdiction=jurisdiction)).encode("utf-8"),
        ),
        *_spool_year_readme_blobs(statutes),
    ]


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
    skip_failures: bool = False,
) -> MarkdownGitExportStats:
    build_markdown_git_spool(
        statute_ids,
        jurisdiction=jurisdiction,
        db_path=db_path,
        include_future=include_future,
        until=until,
        workers=workers,
        skip_failures=skip_failures,
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
    all_replayable = bool(getattr(args, "all_replayable", False))
    statute_ids = tuple(getattr(args, "statute", None) or ())
    if all_replayable and statute_ids:
        raise SystemExit("--all-replayable cannot be combined with --statute")
    if all_replayable:
        if jurisdiction != "fi":
            raise SystemExit("--all-replayable is currently implemented for -j fi")
        statute_ids = statute_ids_from_fi_corpus(substantive_body_only=True)
    elif not statute_ids:
        statute_ids = statute_ids_from_manifest(Path(args.manifest), jurisdiction=jurisdiction)
    limit = getattr(args, "limit", None)
    if limit is not None:
        limit_int = int(limit)
        if limit_int < 1:
            raise SystemExit("--limit must be >= 1")
        statute_ids = statute_ids[:limit_int]
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
    if workers > 1 or spool_db or all_replayable:
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
                skip_failures=all_replayable,
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
                    skip_failures=all_replayable,
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
        f"files={stats.file_count} bytes={stats.byte_count} skipped={stats.skipped_count} "
        f"dest={stats.destination}",
        file=sys.stderr,
    )
