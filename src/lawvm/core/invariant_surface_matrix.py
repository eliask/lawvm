"""Declarative replay diagnostic surface matrix.

Unifies which invariant profiles and replay lint families run on each surface,
and provides shared projection helpers for replay_fold and audit scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from typing import Callable, Iterable, Optional, cast

from lawvm.core.invariant_detectors import (
    InvariantDetectorResult,
    run_descendant_sibling_loss_detector,
    run_same_source_descendant_snapshot_shadow_detector,
)
from lawvm.core.invariant_profiles import (
    ReplayInvariantProfile,
    ReplayTransitionDetectorName,
    ReplayWarningFamily,
    TreeInvariantProfile,
    core_replay_strict_profile,
    structural_product_hierarchical_profile,
    structural_tree_all_profile,
)
from lawvm.core.ir import IRNode, LegalOperation
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE
from lawvm.core.replay_lints import (
    build_flattened_sublist_findings,
    build_label_sequence_gap_findings,
    build_text_duplication_findings,
)

WarningBuilder = Callable[..., list[Finding]]

_WARNING_BUILDERS: dict[ReplayWarningFamily, WarningBuilder] = {
    "text_duplication": build_text_duplication_findings,
    "flattened_sublist_family": build_flattened_sublist_findings,
    "label_sequence_gap": build_label_sequence_gap_findings,
}

_WARNING_META_KEYS: dict[ReplayWarningFamily, str] = {
    "text_duplication": "text_duplication_warnings",
    "flattened_sublist_family": "flattened_sublist_warnings",
    "label_sequence_gap": "label_sequence_gap_warnings",
}

def _format_missing_labels(raw: object) -> str:
    if not isinstance(raw, list):
        return ""
    return ", ".join(str(item) for item in raw[:8])


_WARNING_PRINTERS: dict[ReplayWarningFamily, Callable[[dict[str, object]], str]] = {
    "text_duplication": lambda warning: (
        f"WARNING text duplication: {warning['kind']} {warning['left']} <-> {warning['right']}"
    ),
    "flattened_sublist_family": lambda warning: (
        f"WARNING flattened sublist: {warning['kind']} {warning['path']}"
    ),
    "label_sequence_gap": lambda warning: (
        "WARNING label sequence gap: "
        f"{warning['path']} {warning['node_kind']} missing "
        f"{_format_missing_labels(warning.get('missing_labels'))}"
    ),
}


@dataclass(frozen=True, slots=True)
class ReplayDiagnosticSurface:
    """One replay diagnostic surface with tree + replay invariant profiles."""

    surface_id: str
    tree_profile: TreeInvariantProfile
    replay_profile: ReplayInvariantProfile


FI_REPLAY_FOLD_SURFACE = ReplayDiagnosticSurface(
    surface_id="replay_fold_tree",
    tree_profile=structural_tree_all_profile("replay_fold_tree"),
    replay_profile=core_replay_strict_profile("replay_fold_tree"),
)

FI_REPLAY_PRODUCT_SURFACE = ReplayDiagnosticSurface(
    surface_id="replay_product_tree",
    tree_profile=structural_product_hierarchical_profile("replay_product_tree"),
    replay_profile=core_replay_strict_profile("replay_product_tree"),
)

FI_MATERIALIZED_PRODUCT_SURFACE = ReplayDiagnosticSurface(
    surface_id="materialized_tree",
    tree_profile=structural_product_hierarchical_profile("materialized_tree"),
    replay_profile=core_replay_strict_profile("materialized_tree"),
)

FI_REPLAY_DIAGNOSTIC_SURFACES: tuple[ReplayDiagnosticSurface, ...] = (
    FI_REPLAY_FOLD_SURFACE,
    FI_REPLAY_PRODUCT_SURFACE,
    FI_MATERIALIZED_PRODUCT_SURFACE,
)

_TRANSITION_DETECTOR_RUNNERS = {
    "descendant_sibling_loss": run_descendant_sibling_loss_detector,
    "same_source_descendant_snapshot_shadow": run_same_source_descendant_snapshot_shadow_detector,
}


def record_replay_profile(
    replay_meta_out: dict[str, object],
    surface: ReplayDiagnosticSurface,
) -> None:
    """Record the declarative replay invariant profile for one surface."""
    profiles = replay_meta_out.setdefault("replay_invariant_profiles", [])
    rows = cast(list[dict[str, object]], profiles)
    profile_row = surface.replay_profile.to_dict()
    if profile_row not in rows:
        rows.append(profile_row)


def _warning_detail_rows(findings: Sequence[Finding]) -> list[dict[str, object]]:
    return [
        {key: value for key, value in finding.detail.items() if key != "message"}
        for finding in findings
    ]


def _warning_dedup_key(
    family: ReplayWarningFamily,
    phase: str,
    warning: dict[str, object],
) -> tuple[str, ...]:
    if family == "text_duplication":
        return (
            "text_duplication_warning",
            phase,
            str(warning.get("kind") or ""),
            str(warning.get("left") or ""),
            str(warning.get("right") or ""),
        )
    if family == "flattened_sublist_family":
        return (
            "flattened_sublist_family_warning",
            phase,
            str(warning.get("kind") or ""),
            str(warning.get("path") or ""),
            str(warning.get("node_kind") or ""),
        )
    return (
        "label_sequence_gap_warning",
        phase,
        str(warning.get("kind") or ""),
        str(warning.get("path") or ""),
        str(warning.get("node_kind") or ""),
        str(warning.get("next_label") or ""),
    )


def project_replay_warning_findings(
    *,
    tree: IRNode,
    phase: str,
    source_statute: str,
    warnings: Iterable[ReplayWarningFamily],
    replay_findings: list[Finding],
    replay_meta_out: Optional[dict[str, object]],
    replay_print: Callable[[str], None],
    definition_introducer_predicate: Optional[Callable[[IRNode], bool]] = None,
) -> None:
    """Project replay lint warnings into findings, meta, and replay print.

    ``definition_introducer_predicate`` (optional) is the frontend-supplied
    "is this parent a definition-list introducer?" predicate forwarded to the
    ``flattened_sublist_family`` builder. Finland wires its FI predicate at the
    replay projection call sites; other callers omit it and the kernel applies
    only the suffix-colon (``:``) drafting check (AGENTS.md §2.3 — core hosts
    the hook; it does not interpret frontend-local values).
    """
    for family in warnings:
        builder = _WARNING_BUILDERS.get(family)
        if builder is None:
            continue
        if family == "flattened_sublist_family":
            lint_findings = builder(
                tree,
                phase=phase,
                source_statute=source_statute,
                definition_introducer_predicate=definition_introducer_predicate,
            )
        else:
            lint_findings = builder(tree, phase=phase, source_statute=source_statute)
        if replay_meta_out is not None and lint_findings:
            meta_key = _WARNING_META_KEYS[family]
            replay_meta_out[meta_key] = _warning_detail_rows(lint_findings)
        if not lint_findings:
            continue

        seen = {
            _warning_dedup_key(
                family,
                str(finding.detail.get("phase") or ""),
                {
                    key: value
                    for key, value in finding.detail.items()
                    if key != "message"
                },
            )
            for finding in replay_findings
            if finding.kind
            in {
                "text_duplication_warning",
                "flattened_sublist_family_warning",
                "label_sequence_gap_warning",
            }
        }
        printer = _WARNING_PRINTERS[family]
        for finding in lint_findings:
            warning = {key: value for key, value in finding.detail.items() if key != "message"}
            replay_print(printer(warning))
            key = _warning_dedup_key(family, phase, warning)
            if key not in seen:
                replay_findings.append(finding)
                seen.add(key)


def _transition_detector_finding(
    *,
    result: InvariantDetectorResult,
    detector: ReplayTransitionDetectorName,
    phase: str,
    source_statute: str,
    surface_id: str,
    profile_id: str,
) -> Finding:
    detail = dict(result.detail)
    return Finding(
        kind="REPLAY.TRANSITION_DETECTOR",
        role=OBSERVATION_ROLE,
        stage="replay_apply",
        blocking=False,
        source_statute=source_statute,
        detail={
            "detector": detector,
            "kind": result.kind,
            "path": result.path_text,
            "message": result.message,
            "phase": phase,
            "surface": surface_id,
            "profile_id": profile_id,
            **detail,
        },
    )


def project_transition_detector_findings(
    *,
    before_ir: IRNode,
    operations: Sequence[LegalOperation],
    profile: ReplayInvariantProfile,
    surface: ReplayDiagnosticSurface,
    replay_findings: list[Finding],
    replay_meta_out: dict[str, object] | None,
    replay_print: Callable[[str], None],
    source_statute: str = "",
    phase: str = "replay_apply",
) -> None:
    """Project declared transition detectors for one amendment wave."""
    if not profile.transition_detectors or not operations:
        return

    rows: list[dict[str, object]] = []
    for detector in profile.transition_detectors:
        runner = _TRANSITION_DETECTOR_RUNNERS.get(detector)
        if runner is None:
            continue
        if detector == "descendant_sibling_loss":
            results = cast(
                Callable[
                    [IRNode, Sequence[LegalOperation]],
                    list[InvariantDetectorResult],
                ],
                runner,
            )(before_ir, operations)
        else:
            results = cast(
                Callable[[Sequence[LegalOperation]], list[InvariantDetectorResult]],
                runner,
            )(operations)
        for result in results:
            replay_print(f"WARNING transition detector: {result.message}")
            finding = _transition_detector_finding(
                result=result,
                detector=detector,
                phase=phase,
                source_statute=source_statute,
                surface_id=surface.surface_id,
                profile_id=profile.profile_id,
            )
            replay_findings.append(finding)
            rows.append(dict(finding.detail))

    if replay_meta_out is not None and rows:
        existing = cast(
            list[dict[str, object]],
            replay_meta_out.setdefault("transition_detector_violations", []),
        )
        existing.extend(rows)


__all__ = [
    "FI_MATERIALIZED_PRODUCT_SURFACE",
    "FI_REPLAY_DIAGNOSTIC_SURFACES",
    "FI_REPLAY_FOLD_SURFACE",
    "FI_REPLAY_PRODUCT_SURFACE",
    "ReplayDiagnosticSurface",
    "project_replay_warning_findings",
    "project_transition_detector_findings",
    "record_replay_profile",
]
