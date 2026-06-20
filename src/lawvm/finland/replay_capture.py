"""Replay output-capture policy for the Finland replay entrypoint."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, SupportsIndex


class ReplayLegalOperationCaptureList(list[Any]):
    """LawVM-owned legal-operation capture list with invalidatable local indexes."""

    __slots__ = (
        "base_provision_index_cache",
        "base_target_exists_cache",
        "snapshot_index",
        "timeline_exact_target_index",
        "timeline_latest_target_op_index",
        "timeline_payload_target_index",
        "timeline_target_exists_cache",
    )

    def __init__(self) -> None:
        super().__init__()
        self.base_provision_index_cache: object | None = None
        self.base_target_exists_cache: object | None = None
        self.snapshot_index: object | None = None
        self.timeline_exact_target_index: object | None = None
        self.timeline_latest_target_op_index: object | None = None
        self.timeline_payload_target_index: object | None = None
        self.timeline_target_exists_cache: object | None = None

    def _invalidate_indexes(self) -> None:
        self.base_provision_index_cache = None
        self.snapshot_index = None
        self.timeline_exact_target_index = None
        self.timeline_latest_target_op_index = None
        self.timeline_payload_target_index = None
        self.timeline_target_exists_cache = None

    def append(self, item: Any) -> None:
        super().append(item)

    def extend(self, items: Any) -> None:
        super().extend(items)

    def __iadd__(self, items: Any) -> "ReplayLegalOperationCaptureList":
        super().__iadd__(items)
        return self

    def __setitem__(self, key: Any, value: Any) -> None:
        self._invalidate_indexes()
        super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        self._invalidate_indexes()
        super().__delitem__(key)

    def insert(self, index: SupportsIndex, item: Any) -> None:
        self._invalidate_indexes()
        super().insert(index, item)

    def clear(self) -> None:
        self._invalidate_indexes()
        super().clear()

    def pop(self, index: SupportsIndex = -1) -> Any:
        self._invalidate_indexes()
        return super().pop(index)

    def remove(self, value: Any) -> None:
        self._invalidate_indexes()
        super().remove(value)

    def reverse(self) -> None:
        self._invalidate_indexes()
        super().reverse()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        self._invalidate_indexes()
        super().sort(*args, **kwargs)


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
        legal_operations=(
            request.lo_ops_out
            if request.lo_ops_out is not None
            else ReplayLegalOperationCaptureList()
        ),
        failed_ops=request.failed_ops_out if request.failed_ops_out is not None else [],
    )
