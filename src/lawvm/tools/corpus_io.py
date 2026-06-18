"""Unified Finland corpus I/O for bench CSVs, store access, and oracle reads."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

CorpusKind = Literal["bench_core", "bench_corpus", "batch_test", "line_list", "custom"]


@dataclass(frozen=True, slots=True)
class CorpusSource:
    path: Path
    kind: CorpusKind


@dataclass(frozen=True, slots=True)
class StatuteCorpusRow:
    amendment_count: int
    statute_id: str


def lawvm_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_finland_bench_source(*, prefer_core: bool = True) -> CorpusSource:
    root = lawvm_repo_root()
    candidates: list[tuple[CorpusKind, Path]] = []
    if prefer_core:
        candidates.append(("bench_core", root / "data" / "finland" / "bench_core.csv"))
    candidates.extend(
        [
            ("bench_corpus", root / "data" / "finland" / "bench_corpus.csv"),
            ("batch_test", root / ".tmp" / "batch_test_list.csv"),
        ]
    )
    for kind, path in candidates:
        if path.exists():
            return CorpusSource(path=path, kind=kind)
    return CorpusSource(path=candidates[-1][1], kind="batch_test")


def load_corpus_rows(source: CorpusSource) -> list[StatuteCorpusRow]:
    if not source.path.exists():
        return []
    if source.kind == "line_list":
        return [
            StatuteCorpusRow(amendment_count=0, statute_id=line.strip())
            for line in source.path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    out: list[StatuteCorpusRow] = []
    with source.path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            count_raw, sid = row[0].strip(), row[1].strip()
            if not sid or sid.startswith("#"):
                continue
            try:
                count = int(count_raw)
            except ValueError:
                count = 0
            out.append(StatuteCorpusRow(amendment_count=count, statute_id=sid))
    return out


def load_statute_ids(source: CorpusSource) -> list[str]:
    return [row.statute_id for row in load_corpus_rows(source)]


def normalize_parent_id(raw_id: str) -> str:
    if "-" in raw_id:
        base, suffix = raw_id.rsplit("-", 1)
        if suffix.isdigit():
            return base
    return raw_id


def deduplicate_parent_ids(raw_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw_id in raw_ids:
        parent = normalize_parent_id(raw_id)
        if parent not in seen:
            seen.add(parent)
            out.append(parent)
    return out


def resolve_line_list_source(path: Path) -> CorpusSource:
    return CorpusSource(path=path, kind="line_list")


def get_finland_corpus_store(*, readonly: bool = False) -> Any:
    from lawvm.finland.corpus import get_corpus_store

    if readonly:
        from lawvm.finland.corpus import _get_corpus_store_readonly

        return _get_corpus_store_readonly()
    return get_corpus_store()


def read_oracle_xml(statute_id: str, store: Any) -> bytes | None:
    try:
        return store.read_oracle(statute_id)
    except Exception:
        return None
