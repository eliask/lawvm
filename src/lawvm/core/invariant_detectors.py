"""Typed invariant detector adapters for debugging tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from lawvm.core.replay_lints import build_flattened_sublist_findings
from lawvm.core.tree_ops import (
    TreeInvariantKind,
    find_text_duplication_warnings,
    iter_tree_invariant_violations,
)

InvariantDetectorName = Literal[
    "duplicate_label",
    "illegal_edge",
    "all_tree",
    "text_duplication",
    "flattened_sublist_family",
]
SUPPORTED_INVARIANT_DETECTORS: tuple[InvariantDetectorName, ...] = (
    "duplicate_label",
    "illegal_edge",
    "all_tree",
    "text_duplication",
    "flattened_sublist_family",
)


@dataclass(frozen=True, slots=True)
class InvariantDetectorResult:
    """Typed detector result with a legacy message projection."""

    detector: str
    kind: str
    path_text: str
    message: str
    detail: dict[str, object]


def path_matches_target(path_text: str, target_path: str) -> bool:
    """Return true when a detector path contains the requested target path."""
    if not target_path:
        return True
    path_parts = path_text.split("/")
    target_parts = target_path.split("/")
    n, m = len(path_parts), len(target_parts)
    for i in range(n - m + 1):
        if path_parts[i : i + m] == target_parts:
            return True
    return False


def run_invariant_detector(
    ir: Any,
    detector: str,
    target_path: str = "",
) -> list[InvariantDetectorResult]:
    """Run a structural/lint detector and return typed results.

    The message field intentionally preserves the existing CLI string surface.
    """
    if detector not in SUPPORTED_INVARIANT_DETECTORS:
        supported = ", ".join(SUPPORTED_INVARIANT_DETECTORS)
        raise ValueError(f"unsupported invariant detector {detector!r}; expected one of: {supported}")

    if detector in ("duplicate_label", "illegal_edge", "all_tree"):
        selected_families: set[TreeInvariantKind] | None = None
        if detector == "duplicate_label":
            selected_families = {"duplicate_label", "normalized_duplicate_label"}
        elif detector == "illegal_edge":
            selected_families = {"unexpected_child_kind"}
        return [
            InvariantDetectorResult(
                detector=detector,
                kind=violation.kind,
                path_text=violation.path_text,
                message=violation.message,
                detail={
                    "parent_kind": violation.parent_kind,
                    "child_kind": violation.child_kind,
                    "label": violation.label,
                    "normalized_label": violation.normalized_label,
                    "count": violation.count,
                    "previous_label": violation.previous_label,
                    "next_label": violation.next_label,
                },
            )
            for violation in iter_tree_invariant_violations(ir, families=selected_families)
            if path_matches_target(violation.path_text, target_path)
        ]

    if detector == "text_duplication":
        results: list[InvariantDetectorResult] = []
        for warning in find_text_duplication_warnings(ir):
            kind = str(warning.get("kind") or "?")
            path = str(warning.get("path") or "?")
            left = warning.get("left", "?")
            right = warning.get("right", "?")
            tokens = warning.get("shared_token_count", 0)
            excerpt = str(warning.get("excerpt") or "")[:60]
            message = f"{path}: {kind} {left!r} <-> {right!r} ({tokens} tokens) {excerpt!r}"
            if path_matches_target(path, target_path):
                results.append(
                    InvariantDetectorResult(
                        detector=detector,
                        kind=kind,
                        path_text=path,
                        message=message,
                        detail=dict(warning),
                    )
                )
        return results

    if detector == "flattened_sublist_family":
        results = []
        for finding in build_flattened_sublist_findings(ir, phase="diagnose_phase"):
            warning = finding.detail
            kind = str(warning.get("kind") or "?")
            path = str(warning.get("path") or "?")
            node_kind = str(warning.get("node_kind") or "?")
            raw_sample = warning.get("label_sample")
            sample = list(raw_sample) if isinstance(raw_sample, list) else []
            sample_str = ", ".join(str(item) for item in sample[:8])
            if kind == "flattened_sublist_interleaved":
                raw_families = warning.get("repeated_families")
                families = ", ".join(str(item) for item in (raw_families if isinstance(raw_families, list) else []))
                message = f"{path}: flattened {node_kind} family interleaved ({families}) [{sample_str}]"
            elif kind == "flattened_sublist_reset":
                dominant = str(warning.get("dominant_family") or "?")
                max_before = str(warning.get("max_before_reset") or "?")
                reset_label = str(warning.get("reset_label") or "?")
                message = (
                    f"{path}: flattened {node_kind} {dominant}-family reset at "
                    f"{reset_label!r} (max was {max_before}) [{sample_str}]"
                )
            else:
                message = f"{path}: {kind} {node_kind} [{sample_str}]"
            if path_matches_target(path, target_path):
                results.append(
                    InvariantDetectorResult(
                        detector=detector,
                        kind=kind,
                        path_text=path,
                        message=message,
                        detail=dict(warning),
                    )
                )
        return results

    raise AssertionError(f"unhandled invariant detector {detector!r}")


def run_invariant_detector_messages(
    ir: Any,
    detector: str,
    target_path: str = "",
) -> list[str]:
    """Compatibility projection for legacy CLI output."""
    return [result.message for result in run_invariant_detector(ir, detector, target_path)]
