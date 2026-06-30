"""``lawvm.core.provenance_totality_audit`` — ``PROVENANCE.SOURCE_ANCHOR_MISSING``.

PROVENANCE TOTALITY (stream C). Every emitted :class:`~lawvm.core.ir.LegalOperation`
should trace back to a source instruction — it should carry a *typed source
anchor* so no op is provenance-orphaned. The carrier already exists
(:class:`~lawvm.core.provenance.OperationSource` and its
``source_anchor: SourceAnchor | None``), but populating it is documented as
"owned by the frontend compile" (see :file:`core/ir.py` ~159, ~177-196) — i.e.
it is OPTIONAL today. That optionality is the gap this audit measures: which
emitted ops reach the timeline carrying no provenance footing at all.

This audit makes the totality claim explicit. For each op whose ``source``
lacks any typed provenance footing (defined precisely below), it emits a typed
:class:`~lawvm.core.phase_result.Observation` of kind
``PROVENANCE.SOURCE_ANCHOR_MISSING`` carrying the op identity, action, target,
and exactly which provenance fields were present vs empty.

THE PROVENANCE PREDICATE (what "this op has typed provenance" means). An op is
considered provenance-anchored when at least ONE of the following carriers is
populated — strongest first:

  1. ``op.source.source_anchor`` — the typed byte-span
     :class:`~lawvm.core.provenance.SourceAnchor` ``[off, off+len)`` over the
     raw source bytes (the certified, re-derivable anchor). This is the
     strongest footing and the one the frontends do not yet populate.
  2. ``op.raw_text`` — the per-op verbatim source-clause text (the per-op
     ``clause_text`` that ``compute_source_anchor`` looks up; see
     :file:`core/ir.py` ~174-188). Textual evidence footing, not byte-certified.
  3. ``op.source.raw_text`` — the amendment-level raw instruction language
     (the whole johtolause / enacting clause) carried into lowering.
  4. ``op.source.statute_id`` — the identity of the source instrument that
     drove the op. The weakest footing: it names the source act but not the
     clause. Counted as footing so that an op which at least names its source
     statute is not reported as a *total* orphan; the diagnostic separately
     reports the stronger ``source_anchor``-present rate.

An op is provenance-ORPHANED (and gets one observation) when its ``source`` is
``None``, OR ``source`` is present but ALL four carriers above are empty. This
is the fail-loud "genuinely empty" predicate: an op with no source object and
no per-op raw text traces back to nothing.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). Evidence-plane audit lane: it inspects
passed :class:`~lawvm.core.ir.LegalOperation` carriers, returns
:class:`~lawvm.core.phase_result.Observation` tuples, and **never mutates legal
state**, never fabricates a source anchor, never raises on shape-valid input.
The wire consumer (a future unified seam) decides whether an observation becomes
a strict barrier or a quirks finding — this is NET-NEW core-only audit, not wired
into any apply lane. Mirrors the D7 (``commencement_totality_audit``) and D10
(``compare_eid_parity_audit``) observation-role precedents.

JURISDICTION-NEUTRAL. The op stream and ``OperationSource`` carriers are core
IR; the audit reuses them verbatim with no parallel provenance scheme, so it is
usable by any frontend. The offline diagnostic
(:mod:`lawvm.tools.provenance_totality_report`) runs it over a sample of each
jurisdiction's corpus to surface the per-jurisdiction orphan rate (the real gap).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.phase_result import Observation

# Public finding code, registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
PROVENANCE_SOURCE_ANCHOR_MISSING = "PROVENANCE.SOURCE_ANCHOR_MISSING"

# Audit-stage / owner stamped into the emitted Observations. Mirror the registry
# row's phase/owner so the wire point and the registry agree.
_PROVENANCE_AUDIT_STAGE = "provenance-totality"
_PROVENANCE_AUDIT_OWNER = "provenance_totality_audit"
_PROVENANCE_AUDIT_REASON = "op_carries_no_typed_source_anchor"


def _format_address(address: Optional[LegalAddress]) -> str:
    if address is None:
        return ""
    formatted = str(address)
    if not formatted:
        return repr(address)
    return formatted


def _provenance_fields(op: LegalOperation) -> dict[str, bool]:
    """Presence map of the typed provenance carriers on ``op`` (no values copied).

    Records only booleans (present/empty) — verbatim ``raw_text`` and byte
    spans are intentionally NOT carried into the observation so the surface is
    stable across source-byte revisions (mirrors D7's load-bearing-fields-only
    discipline). The four keys are exactly the provenance-predicate carriers.
    """
    source = op.source
    return {
        "source_anchor": source is not None and source.source_anchor is not None,
        "op_raw_text": bool(op.raw_text),
        "source_raw_text": source is not None and bool(source.raw_text),
        "source_statute_id": source is not None and bool(source.statute_id),
    }


def _op_has_typed_provenance(op: LegalOperation) -> bool:
    """True iff ``op`` carries at least one populated typed provenance footing.

    See the module docstring for the precise predicate. An op with no
    ``source`` object AND no per-op ``raw_text`` has NO footing and returns
    ``False`` (the fail-loud genuinely-empty case).
    """
    return any(_provenance_fields(op).values())


def _build_observation(op: LegalOperation, source_statute: str) -> Observation:
    """Build the typed ``PROVENANCE.SOURCE_ANCHOR_MISSING`` observation.

    Detail carries the op identity, action, target, and the present/empty map of
    every provenance carrier so a triager can answer "which op, which carriers
    were empty" without re-running compilation.
    """
    present = _provenance_fields(op)
    detail: dict[str, Any] = {
        "op_id": op.op_id,
        "action": str(op.action),
        "target": _format_address(op.target),
        # Stable present/empty map of every provenance carrier (booleans only).
        "provenance_present": {key: present[key] for key in sorted(present)},
        "provenance_empty": tuple(sorted(key for key, ok in present.items() if not ok)),
        "has_source": op.source is not None,
        "reason": _PROVENANCE_AUDIT_REASON,
        "owner": _PROVENANCE_AUDIT_OWNER,
    }
    return Observation(
        kind=PROVENANCE_SOURCE_ANCHOR_MISSING,
        stage=_PROVENANCE_AUDIT_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


def assert_op_provenance_totality(
    ops: Sequence[LegalOperation],
    *,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """One :class:`Observation` per op that carries no typed source anchor.

    Args:
        ops: the emitted ``LegalOperation`` stream under audit (any frontend's
            compiled ops). An op filtered out earlier is invisible here; that
            filter owns its receipt accounting (AGENTS.md §1.8).
        source_statute: the base statute id of the ops under audit, carried into
            each observation so a multi-statute bench run can route the finding
            back to its source statute.

    Returns:
        Tuple of Observations, one per provenance-orphaned op, in op-stream order
        (deterministic — the input is already a deterministically-ordered op
        stream). The caller decides whether these become findings (quirks
        default) or strict-mode barriers — this function emits observations only,
        never raises on shape-valid input, never mutates the ops, and never
        fabricates a source anchor.

    Per AGENTS.md §0: an op carrying no provenance footing is surfaced evidence
    that the frontend did not populate a source anchor — it is reported, not
    absorbed. The audit never invents an anchor to make the gap disappear.
    """
    findings: list[Observation] = []
    for op in ops:
        if _op_has_typed_provenance(op):
            continue
        findings.append(_build_observation(op, source_statute))
    return tuple(findings)


__all__ = [
    "PROVENANCE_SOURCE_ANCHOR_MISSING",
    "assert_op_provenance_totality",
]
