"""Declarative replay diagnostic surface matrix.

Unifies which invariant profiles and replay lint families run on each surface,
and provides shared projection helpers for replay_fold and audit scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence, cast

from lawvm.core.invariant_profiles import (
    ReplayInvariantProfile,
    ReplayWarningFamily,
    TreeInvariantProfile,
    core_replay_strict_profile,
    structural_tree_all_profile,
)
from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding
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
) -> None:
    """Project replay lint warnings into findings, meta, and replay print."""
    for family in warnings:
        builder = _WARNING_BUILDERS.get(family)
        if builder is None:
            continue
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


__all__ = [
    "FI_REPLAY_FOLD_SURFACE",
    "ReplayDiagnosticSurface",
    "project_replay_warning_findings",
    "record_replay_profile",
]
