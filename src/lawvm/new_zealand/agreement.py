"""Candidate-vs-oracle agreement for New Zealand source trees.

This comparator is intentionally source-tree based. It can compare any
candidate NZ XML-shaped materialization against an oracle NZ XML snapshot, but
it does not itself produce or bless the candidate. Until NZ replay emits a
candidate materialization, benchmark reports should mark oracle agreement as
blocked rather than treating source-vs-source comparison as replay success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualStatus,
    AgreementSurface,
    agreement_surface_from_residuals,
)
from lawvm.core.source_path_index import duplicate_preserving_source_path_index
from lawvm.new_zealand.acquisition import open_farchive
from lawvm.new_zealand.source_tree import NZSourceDocument, parse_nz_source_document


# Status -> AgreementResidual status. ``exact`` rows agree; a present/present
# divergence or a present/absent topology gap is a residual; the text-exact
# drifts are an editorial frontier (legal text agrees, only the consolidation
# view's ids/history differ).
_COMPARATOR_RESIDUAL_STATUS: dict[str, AgreementResidualStatus] = {
    "exact": "agrees",
    "changed": "residual",
    "oracle_only": "residual",
    "candidate_only": "residual",
    "text_exact_identity_drift": "frontier",
    "text_exact_history_drift": "frontier",
}

_AGREEMENT_FORBIDDEN_SHORTCUTS = (
    "candidate_vs_oracle_agreement_as_replay_authorization",
    "agreement_residual_as_mutation_instruction",
    "oracle_source_tree_as_source_truth",
)


@dataclass(frozen=True)
class NZAgreementRow:
    path: tuple[str, ...]
    agreement_status: str
    candidate_xml_id: str = ""
    oracle_xml_id: str = ""
    candidate_heading: str = ""
    oracle_heading: str = ""
    candidate_history_count: int = 0
    oracle_history_count: int = 0

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "path": list(self.path),
            "agreement_status": self.agreement_status,
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

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for row in self.rows:
            counts[row.agreement_status] = counts.get(row.agreement_status, 0) + 1
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

    def agreement_residuals(self, *, agreement_surface: str = "nz_candidate_oracle_source_tree") -> tuple[AgreementResidual, ...]:
        """Type every comparator row into a core agreement-residual family.

        Each node-status row (changed / oracle_only / candidate_only /
        text_exact_*_drift / exact) is mapped to the shared core family via
        :func:`lawvm.new_zealand.dry_run_oracle.classify_comparator_status_family`,
        so every mismatch row carries a typed disagreement family. This
        comparator never applies an op, so it can never produce a ``replay_bug``;
        its divergences are non-commensurable-surface / topology-granularity /
        oracle-editorial families.
        """

        from lawvm.new_zealand.dry_run_oracle import classify_comparator_status_family

        residuals: list[AgreementResidual] = []
        for index, row in enumerate(self.rows):
            family = classify_comparator_status_family(row.agreement_status)
            residual_status: AgreementResidualStatus = _COMPARATOR_RESIDUAL_STATUS.get(row.agreement_status, "residual")
            path_key = "/".join(row.path) or f"row_{index}"
            residuals.append(
                AgreementResidual(
                    residual_id=f"nz:{self.candidate_version_id or 'candidate'}:{path_key}:{row.agreement_status}",
                    jurisdiction="nz",
                    agreement_surface=agreement_surface,
                    family=family,
                    agreement_residual_status=residual_status,
                    owner_phase="agreement",
                    rule_id=f"nz_agreement_comparator_status_{row.agreement_status}",
                    source_artifact_id=path_key,
                    replay_count=1 if row.candidate_xml_id or row.agreement_status == "candidate_only" else 0,
                    oracle_count=1 if row.oracle_xml_id or row.agreement_status == "oracle_only" else 0,
                    safe_default="classify_candidate_vs_oracle_without_authorizing_replay_or_oracle_truth",
                    forbidden_shortcuts=_AGREEMENT_FORBIDDEN_SHORTCUTS,
                    detail={
                        "agreement_status": row.agreement_status,
                        "candidate_xml_id": row.candidate_xml_id,
                        "oracle_xml_id": row.oracle_xml_id,
                        "candidate_heading": row.candidate_heading,
                        "oracle_heading": row.oracle_heading,
                    },
                )
            )
        return tuple(residuals)

    def agreement_surface(self, *, agreement_surface: str = "nz_candidate_oracle_source_tree") -> AgreementSurface:
        """Project the typed comparator rows into the shared agreement surface."""

        summary = self.summary()
        return agreement_surface_from_residuals(
            self.agreement_residuals(agreement_surface=agreement_surface),
            jurisdiction="nz",
            agreement_surface=agreement_surface,
            materialization_id=f"nz_candidate_source_tree:{self.candidate_version_id or self.candidate_xml_locator}",
            comparison_target_id=f"nz_oracle_source_tree:{self.oracle_version_id or self.oracle_xml_locator}",
            comparison_kind="candidate_source_tree_vs_oracle_source_tree_node_for_node",
            materialization_kind="unknown",
            comparison_materialization_kind="official_consolidation_view",
            exact_ratio=summary["exact_ratio"],
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "nz",
            "report_kind": "candidate_oracle_source_tree_agreement",
            "truth_claim": "candidate_vs_oracle_comparison",
            "replay_claims": False,
            "summary": self.summary(),
            "residual_family_counts": _counts(
                residual.family for residual in self.agreement_residuals()
            ),
            "rows": [row.to_jsonable() for row in self.rows],
            "agreement_surface": self.agreement_surface().to_dict(),
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
                    agreement_status="oracle_only",
                    oracle_xml_id=oracle_node.xml_id,
                    oracle_heading=oracle_node.heading,
                )
            )
        elif candidate_node is not None and oracle_node is None:
            rows.append(
                NZAgreementRow(
                    path=path,
                    agreement_status="candidate_only",
                    candidate_xml_id=candidate_node.xml_id,
                    candidate_heading=candidate_node.heading,
                )
            )
        elif candidate_node is not None and oracle_node is not None:
            rows.append(
                NZAgreementRow(
                    path=path,
                    agreement_status=_node_agreement_status(candidate_node, oracle_node),
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


@dataclass(frozen=True)
class NZActualReplayAgreement:
    """Per-transition agreement of an actual-replay materialization vs the oracle.

    The candidate side is NOT a hand-picked archived XML blob: it is the
    materialized after-document an actual-replay transition produced. The oracle
    side is the archived on-or-after XML the transition was replayed against.
    Every comparator row is typed into a core agreement-residual family, so a
    mismatch is always classifiable (and a source-honest disagreement stays
    distinct from a replay bug).
    """

    work_id: str
    amendment_date_iso: str
    before_version_id: str
    oracle_version_id: str
    report: NZAgreementReport

    def agreement_surface(self) -> AgreementSurface:
        return self.report.agreement_surface(
            agreement_surface="nz_actual_replay_materialized_after_vs_oracle"
        )

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "amendment_date_iso": self.amendment_date_iso,
            "before_version_id": self.before_version_id,
            "oracle_version_id": self.oracle_version_id,
            "summary": self.report.summary(),
            "residual_family_counts": _counts(
                residual.family for residual in self.report.agreement_residuals()
            ),
            "agreement_surface": self.agreement_surface().to_dict(),
            "rows": [row.to_jsonable() for row in self.report.rows],
        }


@dataclass(frozen=True)
class NZActualReplayAgreementReport:
    """Agreement of every actual-replay transition for a work against its oracle.

    This is the Phase-5 surface that consumes ACTUAL replay output: it runs the
    fail-closed actual replay for the work, takes each materialized after-tree
    as the candidate side, compares it to the archived on-or-after oracle, and
    types every mismatch. The refusal lane (everything the replay declined) is
    carried through as the actual-replay report's own typed residuals, so the
    source-honest refusals stay distinct from comparator divergences.
    """

    work_id: str
    families: tuple[str, ...]
    transitions: tuple[NZActualReplayAgreement, ...]
    refusal_family_counts: dict[str, int]
    refusal_residuals: tuple[dict[str, Any], ...]

    def summary(self) -> dict[str, Any]:
        transition_family_counts: dict[str, int] = {}
        for transition in self.transitions:
            for residual in transition.report.agreement_residuals():
                transition_family_counts[residual.family] = (
                    transition_family_counts.get(residual.family, 0) + 1
                )
        return {
            "work_id": self.work_id,
            "families": list(self.families),
            "transitions_compared": len(self.transitions),
            "transition_residual_family_counts": dict(sorted(transition_family_counts.items())),
            "refusal_residual_family_counts": dict(sorted(self.refusal_family_counts.items())),
        }

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "jurisdiction": "nz",
            "report_kind": "actual_replay_materialized_after_vs_oracle_agreement",
            "truth_claim": (
                "actual_replay_materialized_after_tree_vs_archived_on_or_after_xml_oracle"
            ),
            "replay_claims": bool(self.transitions),
            "summary": self.summary(),
            "transitions": [transition.to_jsonable() for transition in self.transitions],
            "refusal_residuals": list(self.refusal_residuals),
        }


def compare_actual_replay_to_oracle(
    *,
    db_path: Path,
    work_id: str,
    families: tuple[str, ...] | None = None,
) -> NZActualReplayAgreementReport:
    """Run actual replay for ``work_id`` and compare each materialized after-tree.

    For every transition the fail-closed actual replay produced, the candidate
    side of the agreement is the materialized after-document (real replay output,
    NOT a hand-picked archived XML blob); the oracle side is the archived
    on-or-after XML the transition was replayed against. The actual-replay
    refusal lane is projected into typed residuals and carried through so the
    source-honest disagreements stay queryable and distinct from a replay bug.
    """

    from lawvm.new_zealand.actual_replay import (
        NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
        build_archived_work_actual_replay,
    )

    replay = build_archived_work_actual_replay(
        db_path,
        work_id,
        families=families or NZ_ACTUAL_REPLAY_DEFAULT_FAMILIES,
    )
    archive = open_farchive(db_path)
    try:
        transitions: list[NZActualReplayAgreement] = []
        for transition in replay.transitions:
            oracle_bytes = archive.get(transition.oracle_xml_locator)
            if oracle_bytes is None:
                raise RuntimeError(
                    "actual-replay oracle XML locator not archived: "
                    f"{transition.oracle_xml_locator}"
                )
            oracle_doc = parse_nz_source_document(
                oracle_bytes,
                xml_locator=transition.oracle_xml_locator,
                version_id=transition.oracle_version_id,
            )
            report = compare_source_documents(transition.materialized_after, oracle_doc)
            transitions.append(
                NZActualReplayAgreement(
                    work_id=work_id,
                    amendment_date_iso=transition.amendment_date_iso,
                    before_version_id=transition.before_version_id,
                    oracle_version_id=transition.oracle_version_id,
                    report=report,
                )
            )
    finally:
        archive.close()

    refusal_residuals = tuple(
        residual.to_dict()
        for residual in replay.agreement_residuals()
        if residual.agreement_residual_status != "agrees"
    )
    refusal_family_counts: dict[str, int] = {}
    for residual in refusal_residuals:
        family = str(residual["family"])
        refusal_family_counts[family] = refusal_family_counts.get(family, 0) + 1

    return NZActualReplayAgreementReport(
        work_id=work_id,
        families=replay.families,
        transitions=tuple(transitions),
        refusal_family_counts=refusal_family_counts,
        refusal_residuals=refusal_residuals,
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
    return duplicate_preserving_source_path_index(
        document.nodes,
        path_of=lambda node: node.path,
        duplicate_id_of=lambda node: node.xml_id,
    )


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main(args: Any) -> None:
    if getattr(args, "from_actual_replay", False):
        if not getattr(args, "work_id", ""):
            raise SystemExit("nz-corpus agreement --from-actual-replay requires --work-id")
        _main_from_actual_replay(args)
        return
    if not args.candidate_xml_locator or not args.oracle_xml_locator:
        raise SystemExit(
            "nz-corpus agreement requires --candidate-xml-locator and "
            "--oracle-xml-locator (or use --from-actual-replay --work-id)"
        )
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
    print(
        "residual_family_counts="
        f"{_counts(residual.family for residual in report.agreement_residuals())}"
    )
    for row in report.rows[: args.limit]:
        if row.agreement_status == "exact":
            continue
        print(
            f"{row.agreement_status}\t{'/'.join(row.path)}\t"
            f"{row.candidate_heading or '-'} -> {row.oracle_heading or '-'}"
        )


def _main_from_actual_replay(args: Any) -> None:
    families = None
    families_arg = getattr(args, "families", "") or ""
    if families_arg and families_arg != "all":
        families = tuple(part.strip() for part in families_arg.split(",") if part.strip())
    report = compare_actual_replay_to_oracle(
        db_path=Path(args.db),
        work_id=args.work_id,
        families=families,
    )
    if args.json:
        print(json.dumps(report.to_jsonable(), ensure_ascii=False, indent=2))
        return
    summary = report.summary()
    print(
        f"work_id={summary['work_id']} families={','.join(summary['families'])} "
        f"transitions_compared={summary['transitions_compared']}"
    )
    print(f"transition_residual_family_counts={summary['transition_residual_family_counts']}")
    print(f"refusal_residual_family_counts={summary['refusal_residual_family_counts']}")
    for transition in report.transitions[: args.limit]:
        trans_summary = transition.report.summary()
        print(
            f"TRANSITION\t{transition.amendment_date_iso}\t"
            f"before={transition.before_version_id}\toracle={transition.oracle_version_id}\t"
            f"agreement_status={trans_summary['agreement_status']}\t"
            f"exact_ratio={trans_summary['exact_ratio']:.6f}\t"
            f"family_counts={_counts(r.family for r in transition.report.agreement_residuals())}"
        )
