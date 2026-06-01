"""Export non-executable UK live target indexes for claim validation."""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.ir import IRNode, IRStatute

if TYPE_CHECKING:
    import argparse


_LIVE_TARGET_INDEX_SCHEMA = "lawvm.uk_live_target_index.v1"
_LEG_BASE = "https://www.legislation.gov.uk"
_DEFAULT_DB_PATH = Path("data/uk_legislation.farchive")
_SOURCE_LOCATORS = {
    "current": "{base}/{statute_id}/data.xml",
    "enacted": "{base}/{statute_id}/enacted/data.xml",
}
_TRANSPARENT_EMPTY_WRAPPER_KINDS = frozenset(
    {
        "body",
        "crossHeading",
        "crossheading",
        "p1group",
        "pgroup",
        "pblock",
    }
)


def _kind_value(node: IRNode) -> str:
    value = node.kind
    return value.value if hasattr(value, "value") else str(value)


def _iter_target_paths_from_node(
    node: IRNode,
    *,
    prefix: tuple[tuple[str, str], ...] = (),
) -> Iterable[str]:
    kind = _kind_value(node)
    label = str(node.label or "")
    if kind in _TRANSPARENT_EMPTY_WRAPPER_KINDS and not label:
        path = prefix
    else:
        path = (*prefix, (kind, label))
        yield "/".join(f"{part_kind}:{part_label}" for part_kind, part_label in path)
    for child in node.children:
        yield from _iter_target_paths_from_node(child, prefix=path)


def _iter_target_path_nodes(
    node: IRNode,
    *,
    prefix: tuple[tuple[str, str], ...] = (),
) -> Iterable[tuple[str, IRNode]]:
    kind = _kind_value(node)
    label = str(node.label or "")
    if kind in _TRANSPARENT_EMPTY_WRAPPER_KINDS and not label:
        path = prefix
    else:
        path = (*prefix, (kind, label))
        yield "/".join(f"{part_kind}:{part_label}" for part_kind, part_label in path), node
    for child in node.children:
        yield from _iter_target_path_nodes(child, prefix=path)


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _node_subtree_fingerprint_payload(node: IRNode) -> dict[str, Any]:
    return {
        "kind": _kind_value(node),
        "label": str(node.label or ""),
        "text": node.text,
        "children": [
            _node_subtree_fingerprint_payload(child)
            for child in node.children
        ],
    }


def _node_subtree_sha256(node: IRNode) -> str:
    payload = json.dumps(
        _node_subtree_fingerprint_payload(node),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256_text(payload)


def target_fingerprints_from_ir(statute: IRStatute) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for root in (statute.body, *statute.supplements):
        for path, node in _iter_target_path_nodes(root):
            fingerprints[path] = {
                "kind": _kind_value(node),
                "label": str(node.label or ""),
                "text_sha256": _sha256_text(node.text),
                "subtree_sha256": _node_subtree_sha256(node),
                "text_preview": node.text[:200],
                "child_count": len(node.children),
            }
    return fingerprints


def target_paths_from_ir(statute: IRStatute) -> tuple[str, ...]:
    paths: set[str] = set()
    paths.update(_iter_target_paths_from_node(statute.body))
    for supplement in statute.supplements:
        paths.update(_iter_target_paths_from_node(supplement))
    return tuple(sorted(paths))


def target_index_row_from_bytes(
    statute_id: str,
    source: str,
    xml_bytes: bytes | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema": _LIVE_TARGET_INDEX_SCHEMA,
        "statute_id": statute_id,
        "source": source,
        "source_url": _SOURCE_LOCATORS[source].format(
            base=_LEG_BASE,
            statute_id=statute_id,
        ),
        "target_paths": [],
        "target_fingerprints": {},
    }
    if not xml_bytes:
        row["source_status"] = "absent"
        return row

    from lawvm.uk_legislation.source_state import classify_uk_statute_xml_content
    from lawvm.uk_legislation.uk_grafter import parse_uk_statute_ir_bytes

    source_state = classify_uk_statute_xml_content(xml_bytes)
    row["source_status"] = source_state.status.value
    row["source_size"] = len(xml_bytes)
    if source_state.status.value != "available":
        return row
    statute = parse_uk_statute_ir_bytes(xml_bytes, statute_id=statute_id)
    row["target_paths"] = list(target_paths_from_ir(statute))
    row["target_fingerprints"] = target_fingerprints_from_ir(statute)
    return row


def _write_jsonl_rows(path: Path, rows: tuple[dict[str, Any], ...]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


def build_live_target_index_rows(
    statute_ids: tuple[str, ...],
    *,
    db_path: Path,
    source: str,
) -> tuple[dict[str, Any], ...]:
    from farchive import Farchive

    archive = Farchive(db_path)
    rows: list[dict[str, Any]] = []
    try:
        for statute_id in statute_ids:
            locator = _SOURCE_LOCATORS[source].format(
                base=_LEG_BASE,
                statute_id=statute_id,
            )
            rows.append(
                target_index_row_from_bytes(
                    statute_id,
                    source,
                    archive.get(locator),
                )
            )
    finally:
        archive.close()
    return tuple(rows)


def live_target_index_report_jsonable(
    rows: tuple[dict[str, Any], ...],
    *,
    source: str,
    db_path: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    total_target_paths = 0
    total_fingerprints = 0
    for row in rows:
        status = str(row.get("source_status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        target_paths = row.get("target_paths")
        if isinstance(target_paths, list | tuple):
            total_target_paths += len(target_paths)
        fingerprints = row.get("target_fingerprints")
        if isinstance(fingerprints, dict):
            total_fingerprints += len(fingerprints)
    summary = {
        "row_count": len(rows),
        "source_status_counts": dict(sorted(status_counts.items())),
        "total_target_paths": total_target_paths,
        "total_target_fingerprints": total_fingerprints,
    }
    evidence_jsonl: dict[str, Any] = {}
    written_paths: tuple[str, ...] = ()
    if out_path is not None:
        evidence_jsonl = {
            "target_index_jsonl": {
                "path": str(out_path),
                "schema": _LIVE_TARGET_INDEX_SCHEMA,
                "row_count": len(rows),
            }
        }
        written_paths = (str(out_path),)
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_live_target_index_report",
        schema="lawvm.uk_live_target_index_report.v1",
        truth_claim="uk_live_target_index_validation_evidence_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "source": source,
            "db_path": str(db_path),
        },
        filtered_summary=summary,
        rows=rows,
        rows_truncated=False,
        evidence_jsonl=evidence_jsonl,
        written_paths=written_paths,
        detail={
            "safe_default": "use_only_as_semantic_claim_validation_precondition",
            "forbidden_shortcuts": (
                "live_target_index_as_target_authority",
                "live_target_index_as_replay_authorization",
                "target_guessing",
            ),
            "next_promotion_requires": (
                "source_instruction_witness",
                "target_identity",
                "payload_identity",
                "mutation_boundary_proof",
            ),
        },
    ).to_dict()


def main(args: "argparse.Namespace") -> None:
    statute_ids = tuple(str(value) for value in getattr(args, "statute_ids", ()) if value)
    if not statute_ids:
        print("error: uk-live-target-index requires at least one statute id", file=sys.stderr)
        sys.exit(2)
    source = str(getattr(args, "source", "current") or "current")
    db_path = Path(str(getattr(args, "db", "") or _DEFAULT_DB_PATH))
    if not db_path.exists():
        print(f"error: UK farchive not found at {db_path}", file=sys.stderr)
        sys.exit(1)
    rows = build_live_target_index_rows(
        statute_ids,
        db_path=db_path,
        source=source,
    )
    out_arg = str(getattr(args, "out", "") or "")
    out_path = Path(out_arg) if out_arg else None
    if out_path is not None:
        _write_jsonl_rows(out_path, rows)
    if bool(getattr(args, "json", False)):
        print(
            json.dumps(
                live_target_index_report_jsonable(
                    rows,
                    source=source,
                    db_path=db_path,
                    out_path=out_path,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if out_path is not None:
        print(f"Wrote {len(rows)} live-target index rows -> {out_path}")
        return
    for row in rows:
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
