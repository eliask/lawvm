"""Export LawVM materializations as a git-fast-import Markdown stream."""

from __future__ import annotations

import argparse
import bisect
import calendar
import dataclasses
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import structural_subtree_hash
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
class PreparedStatute:
    statute_id: str
    engine_id: str
    title: str
    snapshots: tuple[MaterializedSnapshot, ...]
    interlink_rows: tuple[LawvmInterlinkRow, ...]
    interlink_targets: tuple[LawvmInterlinkTargetRow, ...]
    source_url: str = ""

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


@dataclasses.dataclass(frozen=True, slots=True)
class MarkdownGitExportStats:
    statute_count: int
    commit_count: int
    file_count: int
    byte_count: int
    destination: str


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
        bundle = adapter.replay_runner(engine_id, profile=profile)
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
            )
        )
    return tuple(prepared)


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
) -> tuple[FastImportCommit, ...]:
    prepared = tuple(statutes)
    effective_dates = sorted({date_value for statute in prepared for date_value in statute.dates})
    commits: list[FastImportCommit] = []
    for effective_date in effective_dates:
        files: dict[str, bytes] = {}
        active_count = 0
        for statute in prepared:
            snapshot = statute.snapshot_at_or_before(effective_date)
            if snapshot is None:
                continue
            active_count += 1
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
            _render_readme(effective_date, prepared, active_count, jurisdiction=jurisdiction)
        ).encode("utf-8")
        commits.append(
            FastImportCommit(
                effective_date=effective_date,
                message=f"As of {effective_date}",
                files=dict(sorted(files.items())),
                timestamp=_fast_import_timestamp(effective_date),
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
        ident_date = f"{commit.timestamp} +0000"
        yield f"author LawVM <lawvm@example.invalid> {ident_date}\n".encode("ascii")
        yield f"committer LawVM <lawvm@example.invalid> {ident_date}\n".encode("ascii")
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
        for item in contents:
            if item.starts_group and len(lines) > 2 and lines[-1]:
                lines.append("")
            lines.append(f"- [{_escape_markdown(item.title)}](#{item.anchor})")
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
    starts_group: bool = False


def _contents(root: IRNode) -> list[_ContentsItem]:
    items: list[_ContentsItem] = []

    def visit(node: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        counts: dict[str, int] = {}
        for child in node.children:
            kind = str(child.kind)
            if kind in {"part", "chapter", "section"} and child.label:
                counts[kind] = counts.get(kind, 0) + 1
                path = prefix + ((kind, _addr_component_for_node(child, counts[kind])),)
                if len(path) <= 2:
                    items.append(
                        _ContentsItem(
                            anchor=_anchor_for_address(_node_address_string(path)),
                            title=_node_heading(child, fallback_label=child.label or ""),
                            starts_group=kind in {"part", "chapter"},
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
    active_count: int,
    *,
    jurisdiction: str,
) -> str:
    lines = [
        f"# {jurisdiction.upper()} LawVM Markdown Projection",
        "",
        f"- Repository snapshot date: `{effective_date}`",
        f"- Active sample statutes: `{active_count}`",
        f"- Configured statutes: `{len(statutes)}`",
        "",
        "## Statutes",
        "",
    ]
    for statute in sorted(statutes, key=lambda item: item.statute_id):
        lines.append(
            f"- [{_escape_markdown(statute.title)}]("
            f"{_statute_markdown_path(statute.statute_id, jurisdiction=jurisdiction)}) "
            f"`{statute.statute_id}`"
        )
    return "\n".join(lines)


def _data_record(payload: bytes) -> bytes:
    return f"data {len(payload)}\n".encode("ascii") + payload


def _ensure_lf(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _fast_import_timestamp(effective_date: str) -> int:
    parsed = datetime.strptime(effective_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    timestamp = calendar.timegm(parsed.utctimetuple())
    return max(0, timestamp)


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


def main(args: argparse.Namespace) -> None:
    if getattr(args, "repo", None) and getattr(args, "out", "-") not in ("-", None):
        raise SystemExit("--repo cannot be combined with --out; pipe stdout or use --repo")
    jurisdiction = str(getattr(args, "jurisdiction", "fi") or "fi")
    statute_ids = tuple(getattr(args, "statute", None) or ())
    if not statute_ids:
        statute_ids = statute_ids_from_manifest(Path(args.manifest), jurisdiction=jurisdiction)
    if not statute_ids:
        raise SystemExit(f"no {jurisdiction} statutes selected")
    prepared = prepare_markdown_git_export(
        statute_ids,
        jurisdiction=jurisdiction,
        include_future=bool(getattr(args, "include_future", True)),
        until=getattr(args, "until", None),
    )
    commits = build_markdown_git_commits(prepared, jurisdiction=jurisdiction)
    stats = write_fast_import_stream(
        commits,
        out_path=_parse_output_path(getattr(args, "out", "-")),
        repo_path=Path(args.repo) if getattr(args, "repo", None) else None,
        force=bool(getattr(args, "force", False)),
    )
    print(
        "export-markdown-git: "
        f"statutes={stats.statute_count} commits={stats.commit_count} "
        f"files={stats.file_count} bytes={stats.byte_count} dest={stats.destination}",
        file=sys.stderr,
    )
