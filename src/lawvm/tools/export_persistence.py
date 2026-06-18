"""H5 — shared export persistence tail for export_fi_* projections."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ProjectionExportResult:
    row_count: int
    parquet_written: bool
    diag_count: int
    wall_seconds: float


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    if compile_metadata is None:
        raise ValueError(
            "export persistence requires CompileMetadata for substrate-locked writes"
        )
    existing = table.schema.metadata or {}
    meta = dict(existing)
    for key, value in compile_metadata.to_metadata_dict().items():
        meta[key.encode()] = value.encode()
    return table.replace_schema_metadata(meta)


def try_write_parquet(
    path: Path,
    rows: list[dict[str, Any]],
    *,
    schema: Any | None = None,
    compile_metadata: Any = None,
) -> bool:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and schema is not None:
        table = pa.table({col: [] for col in schema.names}, schema=schema)
    else:
        table = pa.Table.from_pylist(rows, schema=schema) if schema else pa.Table.from_pylist(rows)
    table = attach_compile_metadata(table, compile_metadata)
    pq.write_table(table, str(path), compression="zstd")
    return True


def export_projection_tail(
    *,
    name: str,
    data_dir: str | Path,
    rows: list[dict[str, Any]],
    diag_rows: list[dict[str, Any]],
    use_parquet: bool,
    compile_metadata: Any = None,
    parquet_schema: Any | None = None,
    statute_count: int = 0,
    t_start: float | None = None,
) -> ProjectionExportResult:
    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)
    started = t_start if t_start is not None else time.time()

    jsonl_count = write_jsonl(out / f"{name}.jsonl", rows)
    parquet_written = False
    if use_parquet and compile_metadata is not None:
        parquet_written = try_write_parquet(
            out / f"{name}.parquet",
            rows,
            schema=parquet_schema,
            compile_metadata=compile_metadata,
        )

    diag_count = 0
    if diag_rows:
        diag_count = write_jsonl(out / f"{name}_diagnostics.jsonl", diag_rows)

    elapsed = time.time() - started
    rate = statute_count / elapsed if elapsed > 0 and statute_count else 0.0
    suffix = "Parquet+zstd + JSONL" if parquet_written else "JSONL only"
    print(f"  {name}: {jsonl_count:,} rows ({suffix}, {rate:.0f} statutes/s)")
    if diag_count:
        print(f"  {name}_diagnostics: {diag_count:,} rows")
    print(f"  total wall time: {elapsed:.1f}s")

    return ProjectionExportResult(
        row_count=jsonl_count,
        parquet_written=parquet_written,
        diag_count=diag_count,
        wall_seconds=elapsed,
    )


SchemaFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class MultiTableExportSpec:
    name: str
    rows: list[dict[str, Any]]
    parquet_schema: Any | None = None


def export_multi_projection_tail(
    *,
    data_dir: str | Path,
    tables: list[MultiTableExportSpec],
    aux_jsonl: list[tuple[str, list[dict[str, Any]]]] | None = None,
    use_parquet: bool,
    compile_metadata: Any = None,
    t_start: float | None = None,
    label: str = "multi",
) -> dict[str, int]:
    """Write several named projection tables through the shared persistence tail."""
    started = t_start if t_start is not None else time.time()
    counts: dict[str, int] = {}
    for spec in tables:
        counts[spec.name] = export_projection_tail(
            name=spec.name,
            data_dir=data_dir,
            rows=spec.rows,
            diag_rows=[],
            use_parquet=use_parquet,
            compile_metadata=compile_metadata,
            parquet_schema=spec.parquet_schema,
            t_start=started,
        ).row_count
    if aux_jsonl:
        out = Path(data_dir)
        out.mkdir(parents=True, exist_ok=True)
        for name, rows in aux_jsonl:
            if rows:
                write_jsonl(out / f"{name}.jsonl", rows)
                counts[name] = len(rows)
    elapsed = time.time() - started
    print(f"  {label}: {sum(counts.values()):,} total rows in {elapsed:.1f}s")
    return counts
