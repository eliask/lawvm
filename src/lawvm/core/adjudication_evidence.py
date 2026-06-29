"""Shared projection from replay adjudications to corpus evidence rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, cast, overload

from lawvm.core.diagnostic_records import (
    BLOCKING_STRICT_DISPOSITIONS,
    DIAGNOSTIC_DETAIL_ENVELOPE_KEYS,
    diagnostic_detail,
)
from lawvm.core.evidence_contracts import CorpusFindingEvidenceRow
from lawvm.core.quirks_disposition import QuirksDisposition, coerce_quirks_disposition
from lawvm.core.typed_carrier_protocols import (
    CompileAdjudicationProtocol,
    coerce_adjudication,
)


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


@dataclass(frozen=True, slots=True)
class AdjudicationEvidenceInput:
    kind: str
    detail: Mapping[str, Any]
    blocking: bool
    phase: str
    op_id: str = ""
    source_statute: str = ""
    message: str = ""


def _require_blocking(adjudication: Any, raw_blocking: Any, *, kind: str) -> bool:
    if not isinstance(raw_blocking, bool):
        raise TypeError(
            "adjudication carrier must supply a typed bool 'blocking'; "
            f"got {type(raw_blocking)!r} for kind={kind!r}. The emitter must "
            "classify the finding instead of relying on a permissive default."
        )
    return raw_blocking


def _require_phase(raw_phase: Any, *, kind: str) -> str:
    phase = text_or_none(raw_phase)
    if phase is None:
        raise ValueError(
            "adjudication carrier must supply a non-empty 'phase' set by the "
            f"emitter; got {raw_phase!r} for kind={kind!r}. Phase ownership "
            "cannot be guessed from the kind string."
        )
    return phase


@overload
def _adjudication_input(
    adjudication: Mapping[str, Any],
    *,
    default_kind: str,
) -> AdjudicationEvidenceInput: ...


@overload
def _adjudication_input(
    adjudication: CompileAdjudicationProtocol,
    *,
    default_kind: str,
) -> AdjudicationEvidenceInput: ...


def _adjudication_input(
    adjudication: CompileAdjudicationProtocol | Mapping[str, Any],
    *,
    default_kind: str,
) -> AdjudicationEvidenceInput:
    coerce_adjudication(adjudication)
    if isinstance(adjudication, Mapping):
        # ``@overload`` pair above gives ty the explicit case split for the
        # ``Mapping`` vs typed-instance branches at the function boundary; the
        # local helper ``_mapping_view`` below isolates the one residual
        # ``cast`` workaround for the ``Protocol | Mapping[str, Any]`` union
        # narrowing quirk documented in ``typed_carrier_protocols`` (after a
        # bare ``isinstance(.., Mapping)`` narrowing, ty emits a spurious
        # ``Never`` overload variant on ``Mapping.get`` because the Protocol
        # branch narrows to ``CompileAdjudicationProtocol & Top[Mapping]``,
        # whose data attributes like ``kind: str`` shadow the ``Mapping.get``
        # resolution). Runtime behaviour is unchanged: ``isinstance`` is the
        # runtime guard; the cast is the ty-only narrowing hint.
        mapping_view = _mapping_view(adjudication)
        raw_kind = mapping_view.get("kind")
        raw_detail = mapping_view.get("detail")
        raw_op_id = mapping_view.get("op_id")
        raw_source_statute = mapping_view.get("source_statute")
        raw_message = mapping_view.get("message")
        raw_blocking = mapping_view.get("blocking")
        raw_phase = mapping_view.get("phase")
    else:
        raw_kind = getattr(adjudication, "kind", None)
        raw_detail = getattr(adjudication, "detail", None)
        raw_op_id = getattr(adjudication, "op_id", None)
        raw_source_statute = getattr(adjudication, "source_statute", None)
        raw_message = getattr(adjudication, "message", None)
        raw_blocking = getattr(adjudication, "blocking", None)
        raw_phase = getattr(adjudication, "phase", None)
    kind = text_or_none(raw_kind) or default_kind
    return AdjudicationEvidenceInput(
        kind=kind,
        detail=_mapping_or_empty(raw_detail),
        blocking=_require_blocking(adjudication, raw_blocking, kind=kind),
        phase=_require_phase(raw_phase, kind=kind),
        op_id=text_or_none(raw_op_id) or "",
        source_statute=text_or_none(raw_source_statute) or "",
        message=text_or_none(raw_message) or "",
    )


@overload
def _adjudication_kind(
    adjudication: Mapping[str, Any],
    *,
    default_kind: str,
) -> str: ...


@overload
def _adjudication_kind(
    adjudication: CompileAdjudicationProtocol,
    *,
    default_kind: str,
) -> str: ...


def _adjudication_kind(
    adjudication: CompileAdjudicationProtocol | Mapping[str, Any],
    *,
    default_kind: str,
) -> str:
    coerce_adjudication(adjudication)
    if isinstance(adjudication, Mapping):
        # See ``_adjudication_input`` for the ``@overload``-vs-``cast`` note;
        # the ``_mapping_view`` helper centralizes the residual cast for the
        # union narrowing quirk documented in ``typed_carrier_protocols``.
        mapping_view = _mapping_view(adjudication)
        raw_kind = mapping_view.get("kind")
    else:
        raw_kind = getattr(adjudication, "kind", None)
    return text_or_none(raw_kind) or default_kind


def _mapping_view(
    adjudication: CompileAdjudicationProtocol | Mapping[str, Any],
) -> Mapping[str, Any]:
    """Isolate the ``cast(Mapping[str, Any], adjudication)`` workaround.

    Per DEFERRED_ROADMAP.md D4: ty emits a spurious ``Never`` overload variant
    on ``Mapping.get`` resolution after a bare ``isinstance(.., Mapping)``
    narrowing of ``CompileAdjudicationProtocol | Mapping[str, Any]`` — the
    union narrows to ``CompileAdjudicationProtocol & Top[Mapping]`` whose
    data attributes (``kind: str`` etc. from the Protocol) shadow ``Mapping.get``
    resolution (see the module docstring of ``typed_carrier_protocols`` for
    the longer note). The cleanest local fix is the typed local-variable
    annotation ``mapping_view: Mapping[str, Any] = adjudication``; however, ty
    rejects that annotation for the same intersection reason. The remaining
    options were ``typing.overload`` at the function boundary (declared above
    on both ``_adjudication_input`` and ``_adjudication_kind`` for call-site
    clarity — they do NOT resolve the in-body narrowing) and isolating the
    residual ``cast`` in this one helper (this function) so the workaround is
    visible at a single site rather than duplicated across both callers
    (DEFERRED_ROADMAP.md D4 option (c)). Runtime behaviour: ``isinstance`` is
    the runtime guard (assertion mirrors the prior ``if`` branch); ``cast`` is
    a ty-only narrowing hint that returns the same object at runtime.

    The reciprocal options that would eliminate the ``cast`` entirely
    (option (b): make typed carriers explicitly inherit the Protocol under
    ``TYPE_CHECKING``) touch >2 source files beyond ``adjudication_evidence.py``
    and are deferred per the D4 STOP-and-report guard.
    """
    assert isinstance(adjudication, Mapping)
    return cast(Mapping[str, Any], adjudication)


def adjudication_kind_counts(adjudications: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for adjudication in adjudications:
        kind = _adjudication_kind(adjudication, default_kind="unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _resolve_disposition(
    *, blocking: bool, kind: str, raw_strict: str | None
) -> str:
    """Resolve the strict disposition, failing loud on a blocking finding.

    A blocking finding must carry a blocking strict disposition. We never
    silently downgrade it to ``record`` or fabricate ``block`` over a
    non-blocking disposition the emitter explicitly supplied.
    """

    if raw_strict is None:
        return "block" if blocking else "record"
    if blocking and raw_strict not in BLOCKING_STRICT_DISPOSITIONS:
        raise ValueError(
            f"blocking adjudication kind={kind!r} has non-blocking "
            f"strict_disposition={raw_strict!r}; a blocking finding must carry "
            "a blocking disposition."
        )
    return raw_strict


def _build_diagnostic_detail(
    *,
    kind: str,
    blocking: bool,
    phase: str,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    raw_strict = text_or_none(detail.get("strict_disposition"))
    strict_disposition = _resolve_disposition(
        blocking=blocking, kind=kind, raw_strict=raw_strict
    )
    local_detail = {
        str(key): value
        for key, value in detail.items()
        if str(key) not in DIAGNOSTIC_DETAIL_ENVELOPE_KEYS
    }
    return diagnostic_detail(
        rule_id=text_or_none(detail.get("rule_id")) or kind,
        phase=phase,
        blocking=blocking,
        family=text_or_none(detail.get("family")) or "",
        reason=text_or_none(detail.get("reason")) or "",
        message=text_or_none(detail.get("message")) or "",
        strict_disposition=strict_disposition,
        quirks_disposition=coerce_quirks_disposition(
            text_or_none(detail.get("quirks_disposition")) or QuirksDisposition.RECORD
        ),
        detail=local_detail,
    )


def adjudication_record_diagnostic_detail(record: Mapping[str, Any]) -> dict[str, Any]:
    """Build the shared diagnostic envelope for a replay adjudication record.

    This is a projection adapter only. It does not replace frontend-local
    adjudication carriers or classify their extra detail payloads.

    ``blocking`` and ``phase`` are read from the carrier (top-level keys), not
    inferred from ``detail`` with a permissive default nor guessed from the
    kind string. Both are required: the emitter owns enforcement significance
    and phase provenance.
    """

    kind = text_or_none(record.get("kind")) or "compile_adjudication"
    detail = _mapping_or_empty(record.get("detail"))
    blocking = _require_blocking(record, record.get("blocking"), kind=kind)
    phase = _require_phase(record.get("phase"), kind=kind)
    return _build_diagnostic_detail(
        kind=kind, blocking=blocking, phase=phase, detail=detail
    )


def adjudication_diagnostic_detail(
    adjudication: CompileAdjudicationProtocol | Mapping[str, Any],
) -> dict[str, Any]:
    """Build the shared diagnostic envelope for a CompileAdjudication-like object."""

    record = _adjudication_input(adjudication, default_kind="compile_adjudication")
    return _build_diagnostic_detail(
        kind=record.kind,
        blocking=record.blocking,
        phase=record.phase,
        detail=record.detail,
    )


def _adjudication_finding_id(
    *,
    frontend_id: str,
    base_id: str,
    as_of: str,
    index: int,
    kind: str,
    op_id: str,
) -> str:
    suffix = op_id or f"adjudication-{index + 1}"
    return f"{frontend_id}:{base_id}:{as_of}:{kind}:{suffix}"


def adjudication_finding_evidence_rows(
    adjudications: Iterable[CompileAdjudicationProtocol | Mapping[str, Any]],
    *,
    frontend_id: str,
    base_id: str,
    as_of: str,
) -> tuple[CorpusFindingEvidenceRow, ...]:
    """Project replay compile adjudications into shared corpus finding rows."""

    rows: list[CorpusFindingEvidenceRow] = []
    for index, adjudication in enumerate(adjudications):
        record = _adjudication_input(adjudication, default_kind="compile_adjudication")
        detail = _build_diagnostic_detail(
            kind=record.kind,
            blocking=record.blocking,
            phase=record.phase,
            detail=record.detail,
        )
        source_statute = record.source_statute or base_id
        rows.append(
            CorpusFindingEvidenceRow(
                finding_id=_adjudication_finding_id(
                    frontend_id=frontend_id,
                    base_id=base_id,
                    as_of=as_of,
                    index=index,
                    kind=record.kind,
                    op_id=record.op_id,
                ),
                frontend_id=frontend_id,
                family=record.kind,
                rule_id=str(detail["rule_id"]),
                phase=str(detail["phase"]),
                message=record.message or record.kind,
                source_artifact_id=source_statute,
                source_unit_id=record.op_id,
                related_row_ids=(record.op_id,) if record.op_id else (),
                blocking=bool(detail["blocking"]),
                strict_disposition=str(detail["strict_disposition"]),
                quirks_disposition=coerce_quirks_disposition(detail["quirks_disposition"]),
                evidence={
                    "base_id": base_id,
                    "as_of": as_of,
                    "kind": record.kind,
                    "op_id": record.op_id,
                    "detail": dict(record.detail),
                    "diagnostic_detail": detail,
                },
            )
        )
    return tuple(rows)
