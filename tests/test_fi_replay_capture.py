from __future__ import annotations

from lawvm.finland.replay_capture import ReplayCaptureRequest, resolve_replay_capture_sinks


def test_resolve_replay_capture_sinks_allocates_for_full_products() -> None:
    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=None,
            lo_ops_out=None,
            failed_ops_out=None,
            build_full_products=True,
        )
    )

    assert sinks.compiled_ops == []
    assert sinks.legal_operations == []
    assert sinks.failed_ops == []


def test_resolve_replay_capture_sinks_preserves_caller_lists() -> None:
    compiled_ops: list[dict[str, object]] = []
    legal_operations: list[object] = []
    failed_ops: list[object] = []

    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=compiled_ops,
            lo_ops_out=legal_operations,
            failed_ops_out=failed_ops,
            build_full_products=True,
        )
    )

    assert sinks.compiled_ops is compiled_ops
    assert sinks.legal_operations is legal_operations
    assert sinks.failed_ops is failed_ops


def test_resolve_replay_capture_sinks_keeps_lightweight_replay_uncaptured() -> None:
    sinks = resolve_replay_capture_sinks(
        ReplayCaptureRequest(
            compiled_ops_out=None,
            lo_ops_out=None,
            failed_ops_out=None,
            build_full_products=False,
        )
    )

    assert sinks.compiled_ops is None
    assert sinks.legal_operations is None
    assert sinks.failed_ops is None
