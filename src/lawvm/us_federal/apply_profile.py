"""US-federal apply profile + materializer for the unified core apply seam (§3.4).

Design reference: ``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §2.2 / §3.4 / §3.5
(US is a HYBRID: structured ``LegalOperation``s, text-level materializer) and §4
("US — first-class at text granularity NOW").

WHAT THIS IS. The US join onto ``core/apply_seam.apply_op`` at CHAR-SPAN
granularity. US lowers amendment idioms to STRUCTURED ``LegalOperation``s
(``us_federal/amendatory.py``); only its MATERIALIZER is text-level
(``us_federal/dry_run.py::_materialize_one`` does string surgery confined to a
located char span). So US joins ``apply_op`` at the OP level like every tree
frontend — it differs only in which ``Materializer`` + ``RegionMetric`` it plugs
in: the US text materializer + ``core/char_span_metric.CHAR_SPAN_METRIC``.

THE STATE. The seam ``State`` for US is the section's TEXT
(:class:`~lawvm.core.char_span_metric.CharSpanState` wrapping the running
section-text blob plus the located target node text). The materializer threads it
the way the tree frontends thread the ``IRNode`` body.

THE BOUNDARY AUDIT (the point of this task). For each US op, this module:

  1. LOCATES the op's declared target span via US's EXISTING span-location code
     (``dry_run._locate_subsection_text``) — it does NOT reinvent location.
  2. MATERIALIZES via ``_materialize_one`` (the existing text surgery, byte-for-
     byte — this module never changes what text US produces).
  3. AUDITS the char-span mutation-boundary invariant (§3.4) over (before, after,
     located-span): the edited span ⊆ the declared target span AND nothing
     outside the declared span changed
     (``core/char_span_metric.char_span_boundary_holds``). The verdict is emitted
     as ADDITIVE evidence (a finding), never as a change to the US output.

BYTE-IDENTITY / GROUNDING-NEUTRALITY (§2.7, the byte-stable-bench invariant). This
module is an ADDITIVE lane: ``us_apply_profile()`` + :func:`apply_us_op` route a
US op through the seam and run the boundary audit, but ``build_us_dry_run``'s main
composition loop and its AGREE/RESIDUAL rows, refusals, and materialized text are
UNTOUCHED. A caller that does not invoke this lane sees a byte-identical dry run.

PLANE & DISCIPLINE (AGENTS.md §0). The materializer is the ONLY state-mutating
surface and it delegates entirely to ``_materialize_one`` (which never repairs to
the oracle). The boundary audit is pure projection over strings. Typed, frozen,
deterministic; an unlocatable target surfaces a finding, never a silent pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from lawvm.core.apply_seam import (
    ApplyProfile,
    AppliedOp,
    MaterializeResult,
    apply_op,
)
from lawvm.core.char_span_metric import (
    CHAR_SPAN_METRIC,
    CharSpanBoundaryVerdict,
    CharSpanState,
    char_span_boundary_holds,
)
from lawvm.core.coverage import CoverageClaim
from lawvm.core.ir import LegalOperation, OperationSource
from lawvm.core.phase_result import Observation
from lawvm.us_federal.dry_run import (
    UscSection,
    _locate_subsection_text,
    _is_subsection_target,
    _normalize_text,
)

__all__ = [
    "US_CHAR_SPAN_BOUNDARY_FINDING",
    "US_CHAR_SPAN_BOUNDARY_VIOLATION",
    "USMaterializeOutcome",
    "us_apply_profile",
    "apply_us_op",
    "us_coverage_claim_for_op",
]

# Additive observe-mode finding: a US op whose edited char span escaped its
# declared target span (or whose target could not be located). Non-blocking — the
# US dry-run gate stays ``replay_authorized=False`` and the report is unchanged.
US_CHAR_SPAN_BOUNDARY_FINDING = "APPLY.US_CHAR_SPAN_BOUNDARY_FINDING"
# Block-mode companion (a strict profile promotes the escape to a barrier).
US_CHAR_SPAN_BOUNDARY_VIOLATION = "APPLY.US_CHAR_SPAN_BOUNDARY_VIOLATION"

# Surface tag stamped onto the emitted observation detail.
_US_BOUNDARY_STAGE = "apply"
_US_BOUNDARY_OWNER = "us_char_span_boundary_audit"


@dataclass(frozen=True, slots=True)
class USMaterializeOutcome:
    """The typed outcome of materializing one US op (the materializer's signal).

    Mirrors ``_materialize_one``'s return contract so the seam-routed lane carries
    the SAME information the dry-run loop reads, without re-deriving it:

    * ``materialized_text`` — the post-op section text (``_materialize_one``'s
      string output). Equals the input running text on a refusal / residual.
    * ``refused`` — the op was a typed refusal (cannot be faithfully represented
      at the section-text surface). No write landed.
    * ``residual_rule_id`` / ``residual_disposition`` — a match-not-found residual
      signal (the op did not land a write). Empty on a real materialization.
    * ``landed`` — ``True`` iff the op materialized a real text edit (no refusal,
      no residual signal). Drives the boundary audit + receipt emission.
    """

    materialized_text: str
    refused: bool = False
    residual_rule_id: str = ""
    residual_disposition: str = ""
    landed: bool = True


def _located_node_text_for(
    op: LegalOperation, before_section: Optional[UscSection]
) -> Optional[str]:
    """The op's located target node text, or ``None`` for a whole-section op.

    Reuses US's EXISTING span-location code verbatim
    (``dry_run._locate_subsection_text``): for a SUB-section target it returns the
    verbatim before-text of the addressed node (the fragment whose offset in the
    section blob IS the declared char span); for a WHOLE-section op it returns
    ``None`` (the declared span is the whole blob). A sub-section target the
    locator could not resolve also yields ``None`` here, but the materializer's
    own refusal/residual path owns that case — the boundary audit only runs on a
    LANDED op, and a landed sub-section op was located by construction.
    """
    if not _is_subsection_target(op.target):
        return None
    if before_section is None:
        return None
    return _locate_subsection_text(before_section, op.target)


def _us_materializer_factory(
    before_section: Optional[UscSection],
):
    """Build a seam ``Materializer`` over :class:`CharSpanState` for US ops.

    The returned callable is the frontend per-op dispatch the seam wraps: it
    materializes one op through ``_materialize_one`` (the existing text surgery,
    byte-for-byte) and packages the result as a
    :class:`~lawvm.core.apply_seam.MaterializeResult` whose ``new_state`` is the
    post-op :class:`CharSpanState`. It carries the located node text so the
    char-span metric can resolve the declared span downstream.

    DELAYED import of ``_materialize_one`` keeps this factory cheap and avoids a
    module-load cycle (``dry_run`` imports nothing from here).
    """
    from lawvm.us_federal.dry_run import USDryRunRefusal, _materialize_one

    def _materialize(
        state: CharSpanState, op: LegalOperation
    ) -> MaterializeResult[CharSpanState]:
        before_text = state.section_text
        outcome = _materialize_one(op, before_text, before_section=before_section)
        if isinstance(outcome, USDryRunRefusal):
            # Refused: no write landed, state unchanged (mirrors the loop's
            # ``composed_refused`` continue). The refusal is the materializer's
            # finding witness.
            return MaterializeResult(
                new_state=state,
                findings=(outcome,),
                applied=False,
            )
        materialized, signal_rule_id, _signal_disposition = outcome
        if signal_rule_id:
            # Match-not-found residual: the op did not land a write against the
            # running text (mirrors the loop's residual_signal break). State
            # unchanged; the residual rule id is the witness.
            return MaterializeResult(
                new_state=state,
                applied=False,
            )
        # A real materialization: thread the new running text + relocate the
        # post-op node text so a downstream op against the same node sees the
        # running text. The located node text is recomputed lazily by the caller's
        # boundary audit against the BEFORE state, so the new state's located text
        # is left ``None`` (the next op locates against its own before).
        new_text = _normalize_text(materialized)
        return MaterializeResult(
            new_state=CharSpanState(section_text=new_text, located_node_text=None),
            applied=True,
        )

    return _materialize


def us_apply_profile(
    before_section: Optional[UscSection] = None,
    *,
    boundary_mode: str = "observe",
) -> ApplyProfile[CharSpanState]:
    """The US-federal :class:`ApplyProfile` for the char-span apply seam (§3.4).

    Plugs the US text materializer + ``CHAR_SPAN_METRIC``. ``boundary_mode``
    selects the char-span boundary-audit disposition (``"observe"`` non-blocking,
    ``"block"`` barrier, ``"off"`` silent) — the audit itself runs in
    :func:`apply_us_op`, NOT in the seam's IR boundary gate (which the seam skips
    for a non-IR metric, ``apply_seam._is_tree_metric``).

    Receipts are DISABLED on this profile (``emit_receipts=False``): the US
    section-text receipt has its own dedicated, already-shipped emitter
    (``us_federal/us_write_receipts.emit_us_op_receipt``) which the dry-run loop
    drives; the seam's IR-path receipt synthesis would assert an ``IRNode`` state
    and does not apply to the char-span lane. Coverage deltas are likewise left to
    :func:`us_coverage_claim_for_op` + ``assert_coverage_totality`` (the additive
    coverage lane), so the profile's per-op coverage is disabled too.
    """
    return ApplyProfile(
        jurisdiction="us",
        materializer=_us_materializer_factory(before_section),
        region_metric=CHAR_SPAN_METRIC,
        boundary_mode="off",  # the IR gate never runs; the char audit is explicit
        emit_receipts=False,
        emit_coverage=False,
    )


def _boundary_observation(
    verdict: CharSpanBoundaryVerdict,
    *,
    op: LegalOperation,
    source_statute: str,
    is_strict: bool,
) -> Observation:
    """Build the typed char-span boundary observation for an out-of-boundary op.

    Self-evidencing: carries the op id, the declared + observed spans, and whether
    the declared region was unresolved, so a triager can answer "which US op
    edited outside its located target span" without re-running the audit. Mirrors
    the IR boundary finding's load-bearing-fields-only discipline.
    """
    kind = (
        US_CHAR_SPAN_BOUNDARY_VIOLATION if is_strict else US_CHAR_SPAN_BOUNDARY_FINDING
    )
    detail = {
        "op_id": verdict.op_id,
        "target_address": str(op.target),
        "declared_span": list(verdict.declared_span)
        if verdict.declared_span is not None
        else None,
        "observed_span": list(verdict.observed_span),
        "unresolved_declared": verdict.unresolved_declared,
        "owner": _US_BOUNDARY_OWNER,
        "boundary_status": "out_of_boundary",
        "strict_disposition": "barrier" if is_strict else "record",
    }
    return Observation(
        kind=kind,
        stage=_US_BOUNDARY_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


@dataclass(frozen=True, slots=True)
class _USAppliedOp:
    """Result of routing one US op through the char-span apply seam.

    Bundles the seam's :class:`~lawvm.core.apply_seam.AppliedOp` with the
    char-span boundary verdict + any additive observation, so a caller gets both
    the new running text AND the boundary evidence in one typed object.
    """

    applied: AppliedOp[CharSpanState]
    boundary: Optional[CharSpanBoundaryVerdict]
    observations: tuple[Observation, ...]


def apply_us_op(
    section_text: str,
    op: LegalOperation,
    *,
    before_section: Optional[UscSection] = None,
    provenance: Optional[OperationSource] = None,
    source_statute: str = "",
    boundary_mode: str = "observe",
) -> _USAppliedOp:
    """Route one US op through ``apply_op`` at char-span granularity (§3.4).

    Materializes ``op`` against ``section_text`` via the US text materializer
    (``_materialize_one``, byte-for-byte), then runs the char-span mutation-
    boundary audit over (before, after, located-target-span): the edited span ⊆
    the declared target span AND nothing outside the declared span changed
    (``char_span_metric.char_span_boundary_holds``). The audit is ADDITIVE — it
    never changes the materialized text.

    The op's declared target node is located via US's EXISTING
    ``_locate_subsection_text``; a whole-section op's declared span is the whole
    blob. ``boundary_mode`` selects the disposition of an out-of-boundary verdict:
    ``"observe"`` records a non-blocking observation, ``"block"`` records a
    barrier observation, ``"off"`` records none. The verdict is returned either
    way (observations are derived from it).

    Returns a :class:`_USAppliedOp` carrying the seam ``AppliedOp`` (with the new
    :class:`CharSpanState`), the boundary verdict, and the additive observations.
    Pure + deterministic; never mutates the input, never repairs to the oracle.
    """
    located = _located_node_text_for(op, before_section)
    state = CharSpanState(section_text=section_text, located_node_text=located)
    profile = us_apply_profile(before_section, boundary_mode=boundary_mode)
    applied = apply_op(
        state,
        op,
        provenance=provenance,
        profile=profile,
        source_statute=source_statute,
    )
    boundary: Optional[CharSpanBoundaryVerdict] = None
    observations: tuple[Observation, ...] = ()
    if applied.applied:
        after_text = applied.new_state.section_text
        boundary = char_span_boundary_holds(
            section_text,
            after_text,
            state,
            op_id=op.op_id or "",
        )
        if not boundary.within_boundary and boundary_mode != "off":
            observations = (
                _boundary_observation(
                    boundary,
                    op=op,
                    source_statute=source_statute,
                    is_strict=(boundary_mode == "block"),
                ),
            )
    return _USAppliedOp(
        applied=applied, boundary=boundary, observations=observations
    )


def us_coverage_claim_for_op(op: LegalOperation) -> Optional[CoverageClaim]:
    """The additive section-level coverage claim a landed US op contributes.

    US's coverage model is section-level oracle-row algebra (``dry_run.py``):
    the denominator is the oracle-changed sections, the numerator the AGREE rows.
    Feeding it through ``core/coverage_totality.assert_coverage_totality`` (the
    additive audit lane) needs the claimed-section set expressed as
    :class:`~lawvm.core.coverage.CoverageClaim`s. This builds ONE explicit claim
    on the section the op landed on, keyed ``section_<number>`` to match the
    section-level :class:`~lawvm.core.coverage.CoverageUnit` ids the US extractor
    produces. Returns ``None`` when the op carries no section label (the kernel
    never fabricates a claim on an unidentifiable unit, §0).
    """
    section_label = ""
    for kind, label in op.target.path:
        if kind == "section":
            section_label = str(label)
            break
    if not section_label:
        return None
    unit_id = f"section_{section_label}"
    return CoverageClaim(
        claim_kind="explicit",
        target=op.target,
        covered_unit_ids=frozenset({unit_id}),
        evidence=(
            f"op_id={op.op_id or ''}",
            f"action={op.action.value if op.action else ''}",
        ),
    )
