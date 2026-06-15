"""Replay output-capture policy for the Finland replay entrypoint."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReplayCaptureSinks:
    """Concrete lists used while replaying one statute."""

    compiled_ops: list[dict[str, object]] | None
    legal_operations: list[Any] | None
    failed_ops: list[Any] | None


@dataclass(frozen=True, slots=True)
class ReplayCaptureRequest:
    """Requested replay output sinks plus product-construction policy."""

    compiled_ops_out: list[dict[str, object]] | None
    lo_ops_out: list[Any] | None
    failed_ops_out: list[Any] | None
    build_full_products: bool


def resolve_replay_capture_sinks(request: ReplayCaptureRequest) -> ReplayCaptureSinks:
    """Return concrete capture lists needed by the replay/product pipeline.

    Full product construction needs compiled ops, legal operations, and failed
    ops even when the caller did not request those side channels. In lightweight
    replay mode we preserve the caller's omission and avoid creating hidden
    capture lists.
    """
    if not request.build_full_products:
        return ReplayCaptureSinks(
            compiled_ops=request.compiled_ops_out,
            legal_operations=request.lo_ops_out,
            failed_ops=request.failed_ops_out,
        )
    return ReplayCaptureSinks(
        compiled_ops=request.compiled_ops_out if request.compiled_ops_out is not None else [],
        legal_operations=request.lo_ops_out if request.lo_ops_out is not None else [],
        failed_ops=request.failed_ops_out if request.failed_ops_out is not None else [],
    )
