from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
_LAWVM_DIR = _HERE.parent.parent.parent.parent
_OFFICIAL_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_official_fi.jsonl"
_ADJUDICATIONS_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_adjudications_fi.jsonl"
_SOURCES_JSONL = _LAWVM_DIR / "data" / "finland" / "corrigendum_sources_fi.jsonl"

_OFFICIAL_FIELDS = [
    "stable_id",
    "source_pdf",
    "statute_id",
    "amendment_id",
    "lang",
    "correction_index",
    "correction_type",
    "location_desc",
    "wrong_text",
    "correct_text",
    "extraction_source",
    "date_published",
    "llm_extraction",
    "vision_extraction",
    "regex_extraction",
    "parse_error",
    "extract_agreed",
]

_ADJUDICATION_FIELDS = [
    "stable_id",
    "verified_in_source",
]

_SOURCE_FIELDS = [
    "source_pdf",
    "pdf_name",
    "statute_id",
    "amendment_id",
    "lang",
    "date_published",
    "date_status",
    "correction_item_count",
    "sha256",
    "size_bytes",
]

def default_official_records_path() -> Path:
    return _OFFICIAL_JSONL


def default_adjudication_records_path() -> Path:
    return _ADJUDICATIONS_JSONL


def default_source_records_path() -> Path:
    return _SOURCES_JSONL


def default_patch_records_path() -> Path:
    return _OFFICIAL_JSONL


def _load_jsonl_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                records.append(item)
    return records


def _stable_id(record: dict) -> str:
    source_pdf = str(record.get("source_pdf") or "").strip()
    idx = int(record.get("correction_index") or 0)
    return f"{source_pdf}#{idx}"


def _statute_sort_key(statute_id: object) -> tuple[int, int]:
    """statute_id is always YEAR/NUM (e.g. '1999/132')."""
    value = str(statute_id or "").strip()
    if "/" not in value:
        return (9999, 999999)
    a, b = value.split("/", 1)
    if a.isdigit() and b.isdigit():
        return (int(a), int(b))
    return (9999, 999999)


def _amendment_sort_key(amendment_id: object) -> tuple[int, int]:
    """amendment_id is NUM/YEAR (e.g. '41/2013')."""
    value = str(amendment_id or "").strip()
    if "/" not in value:
        return (9999, 999999)
    a, b = value.split("/", 1)
    if a.isdigit() and b.isdigit() and len(b) == 4:
        return (int(b), int(a))
    if a.isdigit() and b.isdigit() and len(a) == 4:
        return (int(a), int(b))
    return (9999, 999999)


def _date_sort_key(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip()
    if not text:
        return (9999, 99, 99)
    parts = text.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        day, month, year = (int(part) for part in parts)
        return (year, month, day)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        y, m, d = text.split("-")
        if y.isdigit() and m.isdigit() and d.isdigit():
            return (int(y), int(m), int(d))
    return (9999, 99, 99)


def _official_sort_key(record: dict) -> tuple:
    # statute first — keeps all corrections for one statute contiguous in the file
    # then date — chronological audit trail within a statute
    # then amendment, index, pdf — deterministic tiebreaking
    return (
        _statute_sort_key(record.get("statute_id")),
        _date_sort_key(record.get("date_published")),
        _amendment_sort_key(record.get("amendment_id")),
        int(record.get("correction_index") or 0),
        str(record.get("source_pdf") or ""),
    )


def _source_sort_key(record: dict) -> tuple[tuple[int, int, int], int, int, str]:
    date_published = _date_sort_key(record.get("date_published"))
    amendment_year, amendment_num = _amendment_sort_key(record.get("amendment_id"))
    return (
        date_published,
        amendment_year,
        amendment_num,
        str(record.get("source_pdf") or ""),
    )


def _merge_official_and_adjudications(
    official_records: list[dict],
    adjudication_records: list[dict],
) -> list[dict]:
    adjudications_by_id = {
        str(row.get("stable_id") or ""): row for row in adjudication_records if row.get("stable_id")
    }
    combined: list[dict] = []
    for official in official_records:
        stable_id = str(official.get("stable_id") or "")
        row = dict(official)
        row.update(adjudications_by_id.get(stable_id, {}))
        row["stable_id"] = stable_id
        combined.append(row)
    return combined


def load_official_records(path: Optional[Path] = None) -> list[dict]:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    if target.exists():
        records = _load_jsonl_records(target)
        for row in records:
            row["stable_id"] = str(row.get("stable_id") or _stable_id(row))
        return records
    return []


def load_adjudication_records(path: Optional[Path] = None) -> list[dict]:
    target = Path(path) if path is not None else _ADJUDICATIONS_JSONL
    if target.exists():
        records = _load_jsonl_records(target)
        for row in records:
            row["stable_id"] = str(row.get("stable_id") or "")
        return records
    return []


def load_source_records(path: Optional[Path] = None) -> list[dict]:
    target = Path(path) if path is not None else _SOURCES_JSONL
    if target.exists():
        return _load_jsonl_records(target)
    return []


def load_patch_records(path: Optional[Path] = None) -> list[dict]:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    if target.exists():
        if target.name == _ADJUDICATIONS_JSONL.name:
            official = load_official_records()
            adjudications = load_adjudication_records(target)
            return _merge_official_and_adjudications(official, adjudications)
        adjudications_path = target.with_name(_ADJUDICATIONS_JSONL.name)
        official = load_official_records(target)
        adjudications = load_adjudication_records(adjudications_path)
        return _merge_official_and_adjudications(official, adjudications)
    if path is None:
        official = load_official_records()
        adjudications = load_adjudication_records()
        if official:
            return _merge_official_and_adjudications(official, adjudications)
    return []


def write_official_records(records: list[dict], path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else _OFFICIAL_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=_official_sort_key):
        row = {field: record.get(field) for field in _OFFICIAL_FIELDS}
        row["stable_id"] = str(record.get("stable_id") or _stable_id(record))
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def write_adjudication_records(records: list[dict], path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else _ADJUDICATIONS_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=lambda r: str(r.get("stable_id") or "")):
        row = {field: record.get(field) for field in _ADJUDICATION_FIELDS}
        row["stable_id"] = str(record.get("stable_id") or "")
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target


def write_source_records(records: list[dict], path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else _SOURCES_JSONL
    target.parent.mkdir(parents=True, exist_ok=True)
    normalized = []
    for record in sorted(records, key=_source_sort_key):
        row = {field: record.get(field) for field in _SOURCE_FIELDS}
        normalized.append(row)
    with target.open("w", encoding="utf-8") as f:
        for record in normalized:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return target
