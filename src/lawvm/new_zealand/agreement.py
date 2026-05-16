"""Candidate-vs-oracle agreement for New Zealand source trees.

This comparator is intentionally source-tree based. It can compare any
candidate NZ XML-shaped materialization against an oracle NZ XML snapshot, but
it does not itself produce or bless the candidate. Until NZ replay emits a
candidate materialization, benchmark reports should mark oracle agreement as
blocked rather than treating source-vs-source comparison as replay success.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.source_tree import NZSourceDocument, parse_nz_source_document


@dataclass(frozen=True)
class NZAgreementRow:
    path: tuple[str, ...]
    status: str
    candidate_xml_id: str = ""
    oracle_xml_id: str = ""
    candidate_heading: str = ""
    oracle_heading: str = ""
    candidate_history_count: int = 0
    oracle_history_count: int = 0

    def to_jsonable(self) -> dict[str, object]:
        return {
            "path": list(self.path),
            "status": self.status,
            "candidate_xml_id": self.candidate_xml_id,
            "oracle_xml_id": self.oracle_xml_id,
            "candidate_heading": self.candidate_heading,
            "oracle_heading": self.oracle_heading,
            "candidate_history_count": self.candidate_history_count,
            "oracle_history_count": self.oracle_history_count,
        }


@dataclass(frozen=True)
class NZAgreementReport:
    candidate_version_id: str
    oracle_version_id: str
    candidate_xml_locator: str
    oracle_xml_locator: str
    rows: tuple[NZAgreementRow, ...]

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        total = len(self.rows)
        exact = counts.get("exact", 0)
        return {
            "candidate_version_id": self.candidate_version_id,
            "oracle_version_id": self.oracle_version_id,
            "candidate_xml_locator": self.candidate_xml_locator,
            "oracle_xml_locator": self.oracle_xml_locator,
            "rows": total,
            "status_counts": counts,
            "exact_ratio": exact / total if total else 1.0,
            "agreement_status": "exact" if exact == total else "mismatch",
        }

    def to_jsonable(self) -> dict[str, object]:
        return {
            "jurisdiction": "nz",
            "report_kind": "candidate_oracle_source_tree_agreement",
            "truth_claim": "candidate_vs_oracle_comparison",
            "replay_claims": False,
            "summary": self.summary(),
            "rows": [row.to_jsonable() for row in self.rows],
        }


def compare_source_documents(
    candidate: NZSourceDocument,
    oracle: NZSourceDocument,
) -> NZAgreementReport:
    candidate_nodes = _node_index(candidate)
    oracle_nodes = _node_index(oracle)
    rows: list[NZAgreementRow] = []
    for path in sorted(candidate_nodes.keys() | oracle_nodes.keys()):
        candidate_node = candidate_nodes.get(path)
        oracle_node = oracle_nodes.get(path)
        if candidate_node is None and oracle_node is not None:
            rows.append(
                NZAgreementRow(
                    path=path,
                    status="oracle_only",
                    oracle_xml_id=oracle_node.xml_id,
                    oracle_heading=oracle_node.heading,
                )
            )
        elif candidate_node is not None and oracle_node is None:
            rows.append(
                NZAgreementRow(
                    path=path,
                    status="candidate_only",
                    candidate_xml_id=candidate_node.xml_id,
                    candidate_heading=candidate_node.heading,
                )
            )
        elif candidate_node is not None and oracle_node is not None:
            rows.append(
                NZAgreementRow(
                    path=path,
                    status=_node_agreement_status(candidate_node, oracle_node),
                    candidate_xml_id=candidate_node.xml_id,
                    oracle_xml_id=oracle_node.xml_id,
                    candidate_heading=candidate_node.heading,
                    oracle_heading=oracle_node.heading,
                    candidate_history_count=len(candidate_node.history),
                    oracle_history_count=len(oracle_node.history),
                )
            )
    return NZAgreementReport(
        candidate_version_id=candidate.version_id,
        oracle_version_id=oracle.version_id,
        candidate_xml_locator=candidate.xml_locator,
        oracle_xml_locator=oracle.xml_locator,
        rows=tuple(rows),
    )


def compare_archived_xml(
    *,
    db_path: Path,
    candidate_xml_locator: str,
    oracle_xml_locator: str,
    candidate_version_id: str = "",
    oracle_version_id: str = "",
) -> NZAgreementReport:
    archive = open_farchive(db_path)
    try:
        candidate_bytes = archive.get(candidate_xml_locator)
        oracle_bytes = archive.get(oracle_xml_locator)
    finally:
        archive.close()
    if candidate_bytes is None:
        raise RuntimeError(f"candidate XML locator not archived: {candidate_xml_locator}")
    if oracle_bytes is None:
        raise RuntimeError(f"oracle XML locator not archived: {oracle_xml_locator}")
    return compare_source_documents(
        parse_nz_source_document(
            candidate_bytes,
            xml_locator=candidate_xml_locator,
            version_id=candidate_version_id,
        ),
        parse_nz_source_document(
            oracle_bytes,
            xml_locator=oracle_xml_locator,
            version_id=oracle_version_id,
        ),
    )


def _node_agreement_status(candidate: Any, oracle: Any) -> str:
    legal_text_agrees = (
        candidate.heading == oracle.heading
        and candidate.deletion_status == oracle.deletion_status
        and candidate.text == oracle.text
    )
    if not legal_text_agrees:
        return "changed"
    if candidate.xml_id and oracle.xml_id and candidate.xml_id != oracle.xml_id:
        return "text_exact_identity_drift"
    if tuple(witness.text for witness in candidate.history) != tuple(witness.text for witness in oracle.history):
        return "text_exact_history_drift"
    return "exact"


def _node_index(document: NZSourceDocument) -> dict[tuple[str, ...], Any]:
    path_counts: Counter[tuple[str, ...]] = Counter(node.path for node in document.nodes)
    seen: Counter[tuple[str, ...]] = Counter()
    indexed: dict[tuple[str, ...], Any] = {}
    for node in document.nodes:
        if path_counts[node.path] == 1:
            key = node.path
        else:
            seen[node.path] += 1
            suffix = node.xml_id or f"ordinal:{seen[node.path]}"
            key = (*node.path, f"source-duplicate:{suffix}")
        indexed[key] = node
    return indexed


def main(args: Any) -> None:
    report = compare_archived_xml(
        db_path=Path(args.db),
        candidate_xml_locator=args.candidate_xml_locator,
        oracle_xml_locator=args.oracle_xml_locator,
        candidate_version_id=args.candidate_version_id or "",
        oracle_version_id=args.oracle_version_id or "",
    )
    if args.json:
        print(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2))
        return
    summary = report.summary()
    print(
        f"agreement_status={summary['agreement_status']} rows={summary['rows']} "
        f"exact_ratio={summary['exact_ratio']:.6f} status_counts={summary['status_counts']}"
    )
    for row in report.rows[: args.limit]:
        if row.status == "exact":
            continue
        print(
            f"{row.status}\t{'/'.join(row.path)}\t"
            f"{row.candidate_heading or '-'} -> {row.oracle_heading or '-'}"
        )
