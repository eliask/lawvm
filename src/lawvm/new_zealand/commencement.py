"""Commencement / brought-into-force witnesses as TEMPORAL-STATE effects.

NZ consolidated history notes carry "brought into force" rows, e.g.::

    Section 1(2) : this Act brought into force , on 1 January 1955 , by the
    Patents Act Commencement Order 1954 (SR 1954/220).

These are NOT text mutations. They change a provision's IN-FORCE STATUS at a
date; they do not insert, replace, or repeal any text. The body text of the
provision is identical before and after the commencement. Coercing them into a
text/structural mutation (insert/replace/repeal) would be unsound — there is no
text delta to materialize against the on-or-after XML oracle.

This module therefore models commencement as its own typed temporal-state
effect, kept entirely off the text-mutation replay path (``dry_run`` /
``actual_replay`` never see these rows). One determinate commencement witness
becomes one :class:`NZCommencementRecord`: a (target provision address +
commencement date + commencing-instrument evidence) row that records in-force
status WITHOUT altering text. The honesty boundary mirrors the rest of the NZ
frontend: a witness whose target address is not a determinate candidate, or
whose effective date is not determinate, is REFUSED as typed frontier residue
rather than recorded against a guessed address/date.

The records are projected into the shared agreement-residual surface so a
commencement witness is no longer mis-bucketed as a generic UNSUPPORTED text op
in the operation-surface refusal lane. Because commencement is not a text
comparison, the surface NEVER claims a text-slice agreement (``agrees``): a
recorded commencement is typed ``non_commensurable_surface`` (a determinate
temporal-state record on a non-text axis), and frontier residue is typed
``accepted_non_executable_frontier`` — both with status ``frontier``, kept
distinct by family and by the attached record payload. There is no text
similarity claim and no tree is materialized.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lawvm.core.agreement_residual import (
    AgreementResidual,
    agreement_surface_from_residuals,
)
from lawvm.new_zealand.operation_surface import (
    NZOperationSurfaceReport,
    NZOperationWitnessRow,
    build_archived_work_operation_surface,
)


# The operation family the operation surface assigns to commencement witnesses.
NZ_COMMENCEMENT_OPERATION_FAMILY = "brought into force"

# Rule ids for the typed outcomes. A recorded commencement is a sound
# temporal-state effect; the refusal rule ids are distinct named diagnostics so a
# refusal is never an opaque or silent skip.
NZ_COMMENCEMENT_RECORDED_RULE_ID = (
    "nz_commencement_recorded_in_force_status_temporal_state_effect"
)
NZ_COMMENCEMENT_REFUSED_TARGET_NOT_DETERMINATE_RULE_ID = (
    "nz_commencement_refused_target_address_not_determinate_candidate"
)
NZ_COMMENCEMENT_REFUSED_DATE_NOT_DETERMINATE_RULE_ID = (
    "nz_commencement_refused_effective_date_not_determinate_iso"
)

# Forbidden shortcuts that this surface must never take. Recorded verbatim so the
# honesty boundary is auditable.
_FORBIDDEN_SHORTCUTS = (
    "commencement_as_text_or_structural_mutation",
    "guessed_target_address_for_indeterminate_commencement",
    "guessed_effective_date_for_undated_commencement",
)


@dataclass(frozen=True)
class NZCommencementRecord:
    """One recorded in-force / commencement temporal-state effect.

    This records that ``target_address`` was brought into force on
    ``commencement_date_iso`` by ``commencing_instrument``. It carries NO text
    payload and produces NO tree mutation: the in-force status is the effect.
    """

    row_id: str
    target_address: str
    target_source_path: tuple[str, ...]
    commencement_date_iso: str
    commencing_instrument: str
    commencing_work_id: str
    # The verbatim subject of the commencement as stated in the history note
    # ("this Act", "sections 4-6", "the Schedule"). This is the source-honest
    # scope statement; the resolved ``target_address`` is the operation surface's
    # candidate for the note's attached provision.
    subject_text: str
    witness_text: str
    source_xml_id: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "effect_kind": "commencement_in_force_status",
            "is_text_mutation": False,
            "target_address": self.target_address,
            "target_source_path": list(self.target_source_path),
            "commencement_date_iso": self.commencement_date_iso,
            "commencing_instrument": self.commencing_instrument,
            "commencing_work_id": self.commencing_work_id,
            "subject_text": self.subject_text,
            "witness_text": self.witness_text,
            "source_xml_id": self.source_xml_id,
            "rule_id": NZ_COMMENCEMENT_RECORDED_RULE_ID,
        }


@dataclass(frozen=True)
class NZCommencementRefusal:
    """A commencement witness that could not be recorded — typed frontier residue.

    Fail-closed: nothing is recorded for it. ``rule_id`` is a distinct named
    diagnostic so the refusal is never an opaque skip.
    """

    row_id: str
    rule_id: str
    message: str
    target_address_status: str
    target_surface_status: str
    commencement_date_iso: str
    witness_text: str
    source_xml_id: str

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "rule_id": self.rule_id,
            "message": self.message,
            "target_address_status": self.target_address_status,
            "target_surface_status": self.target_surface_status,
            "commencement_date_iso": self.commencement_date_iso,
            "witness_text": self.witness_text,
            "source_xml_id": self.source_xml_id,
        }


@dataclass(frozen=True)
class NZCommencementReport:
    """Commencement temporal-state surface for one archived NZ work.

    ``records`` are the determinate, recorded in-force effects; ``refusals`` are
    the typed frontier residue. The counts are always separable so the number of
    correctly-handled commencement witnesses is distinct from the residual tail.
    This surface makes NO replay claim and NO text-agreement claim.
    """

    work_id: str
    records: tuple[NZCommencementRecord, ...]
    refusals: tuple[NZCommencementRefusal, ...]
    forbidden_shortcuts: tuple[str, ...] = _FORBIDDEN_SHORTCUTS

    def witness_count(self) -> int:
        return len(self.records) + len(self.refusals)

    def summary(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "commencement_witnesses": self.witness_count(),
            "recorded": len(self.records),
            "frontier_residue": len(self.refusals),
            "refusal_rule_counts": _counts(refusal.rule_id for refusal in self.refusals),
            "replay_claims": False,
            "text_agreement_claims": False,
            "is_text_mutation_family": False,
        }

    def agreement_residuals(self) -> tuple[AgreementResidual, ...]:
        residuals: list[AgreementResidual] = []
        for record in self.records:
            residuals.append(
                AgreementResidual(
                    residual_id=f"{self.work_id}:{record.row_id}:commencement_recorded",
                    jurisdiction="nz",
                    agreement_surface="nz_commencement",
                    # A determinate temporal-state record on a NON-text axis: it
                    # is correctly typed/handled, but it is not commensurable with
                    # a text-slice comparison, so it never claims ``agrees``.
                    family="non_commensurable_surface",
                    agreement_residual_status="frontier",
                    owner_phase="commencement",
                    rule_id=NZ_COMMENCEMENT_RECORDED_RULE_ID,
                    source_artifact_id=record.source_xml_id or record.row_id,
                    replay_count=0,
                    oracle_count=0,
                    safe_default="record_in_force_status_without_text_mutation",
                    forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
                    detail={
                        "effect_kind": "commencement_in_force_status",
                        "is_text_mutation": False,
                        "target_address": record.target_address,
                        "commencement_date_iso": record.commencement_date_iso,
                        "commencing_instrument": record.commencing_instrument,
                        "subject_text": record.subject_text,
                    },
                )
            )
        for refusal in self.refusals:
            residuals.append(
                AgreementResidual(
                    residual_id=f"{self.work_id}:{refusal.row_id}:{refusal.rule_id}",
                    jurisdiction="nz",
                    agreement_surface="nz_commencement",
                    family="accepted_non_executable_frontier",
                    agreement_residual_status="frontier",
                    owner_phase="commencement",
                    rule_id=refusal.rule_id,
                    source_artifact_id=refusal.source_xml_id or refusal.row_id,
                    replay_count=0,
                    oracle_count=0,
                    safe_default="record_in_force_status_without_text_mutation",
                    forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
                    detail={
                        "effect_kind": "commencement_in_force_status",
                        "is_text_mutation": False,
                        "target_address_status": refusal.target_address_status,
                        "target_surface_status": refusal.target_surface_status,
                        "commencement_date_iso": refusal.commencement_date_iso,
                        "message": refusal.message,
                    },
                )
            )
        return tuple(residuals)

    def agreement_surface(self) -> dict[str, Any]:
        residuals = self.agreement_residuals()
        surface = agreement_surface_from_residuals(
            residuals,
            jurisdiction="nz",
            agreement_surface="nz_commencement",
            materialization_id=f"nz_commencement:{self.work_id}",
            comparison_target_id=f"nz_source_history_note:{self.work_id}",
            comparison_kind="commencement_in_force_status_from_source_history_note",
            # The recorded effect is a legal-state fact (in-force status), not a
            # text materialization; there is no oracle text to compare against.
            materialization_kind="legal_text_state",
            comparison_materialization_kind="source_as_enacted",
            exact_ratio=None,
        )
        return surface.to_dict()

    def to_jsonable(self, *, summary_only: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jurisdiction": "nz",
            "report_kind": "commencement_temporal_state",
            "truth_claim": (
                "commencement_in_force_status_temporal_state_effects_from_source_"
                "history_notes_no_text_mutation"
            ),
            "replay_claims": False,
            "dry_run_claims": False,
            "text_agreement_claims": False,
            "fail_closed": True,
            "forbidden_shortcuts": list(self.forbidden_shortcuts),
            "summary": self.summary(),
        }
        if summary_only:
            return payload
        payload["records"] = [record.to_jsonable() for record in self.records]
        payload["refusals"] = [refusal.to_jsonable() for refusal in self.refusals]
        payload["agreement_surface"] = self.agreement_surface()
        return payload


def build_commencement_surface(
    operation_surface: NZOperationSurfaceReport,
    *,
    work_id: str = "",
) -> NZCommencementReport:
    """Type every commencement witness into a temporal-state record or residue.

    Determinacy rule (fail-closed): a commencement witness is RECORDED only when
    its operation-surface target-address candidate is a determinate ``candidate``
    AND its effective date is a determinate ISO date. Otherwise it is typed
    frontier residue with a distinct named diagnostic. The body text is never
    read, mutated, or compared — commencement does not change text.
    """

    resolved_work_id = work_id or operation_surface.work_id
    records: list[NZCommencementRecord] = []
    refusals: list[NZCommencementRefusal] = []
    for row in operation_surface.rows:
        if row.operation_family != NZ_COMMENCEMENT_OPERATION_FAMILY:
            continue
        outcome = _classify_commencement_row(row)
        if isinstance(outcome, NZCommencementRecord):
            records.append(outcome)
        else:
            refusals.append(outcome)
    return NZCommencementReport(
        work_id=resolved_work_id,
        records=tuple(records),
        refusals=tuple(refusals),
    )


def build_archived_work_commencement_surface(db_path: Path, work_id: str) -> NZCommencementReport:
    operation_surface = build_archived_work_operation_surface(db_path, work_id)
    return build_commencement_surface(operation_surface, work_id=work_id)


def _classify_commencement_row(
    row: NZOperationWitnessRow,
) -> NZCommencementRecord | NZCommencementRefusal:
    # Date determinacy: a commencement records an in-force status AT a date; with
    # no determinate ISO date there is nothing to record against, so refuse.
    if not row.amendment_date_iso:
        return NZCommencementRefusal(
            row_id=row.row_id,
            rule_id=NZ_COMMENCEMENT_REFUSED_DATE_NOT_DETERMINATE_RULE_ID,
            message=(
                "commencement refused because the witness carries no determinate "
                "ISO effective date to record an in-force status against"
            ),
            target_address_status=row.target_address_candidate.target_address_status,
            target_surface_status=row.target_surface_status,
            commencement_date_iso="",
            witness_text=row.witness_text,
            source_xml_id=row.source_xml_id,
        )
    # Target determinacy: record only against a determinate target-address
    # candidate; never guess an address for a non-current skeleton node or an
    # unparsed target hint.
    if row.target_address_candidate.target_address_status != "candidate":
        return NZCommencementRefusal(
            row_id=row.row_id,
            rule_id=NZ_COMMENCEMENT_REFUSED_TARGET_NOT_DETERMINATE_RULE_ID,
            message=(
                "commencement refused because the target provision address is not a "
                f"determinate candidate (status={row.target_address_candidate.target_address_status})"
            ),
            target_address_status=row.target_address_candidate.target_address_status,
            target_surface_status=row.target_surface_status,
            commencement_date_iso=row.amendment_date_iso,
            witness_text=row.witness_text,
            source_xml_id=row.source_xml_id,
        )
    return NZCommencementRecord(
        row_id=row.row_id,
        target_address=row.target_address_candidate.address,
        target_source_path=row.source_path,
        commencement_date_iso=row.amendment_date_iso,
        commencing_instrument=row.amending_legislation,
        commencing_work_id=row.amending_work_id,
        subject_text=_commencement_subject_text(row),
        witness_text=row.witness_text,
        source_xml_id=row.source_xml_id,
    )


def _commencement_subject_text(row: NZOperationWitnessRow) -> str:
    """The verbatim subject brought into force, read from the witness text.

    The history note shape is ``<scope> brought into force , on <date> , by
    <instrument>``. We take the run preceding "brought into force" as the
    source-honest subject ("this Act", "sections 4-6", "the Schedule"). This is
    recorded as provenance only; it never overrides the resolved target address.
    """

    text = " ".join(row.witness_text.split())
    marker = "brought into force"
    lowered = text.lower()
    index = lowered.find(marker)
    if index <= 0:
        return ""
    head = text[:index].strip()
    # Drop a leading provision label and colon ("Section 1(2) : ...") if present.
    if ":" in head:
        head = head.split(":", 1)[1].strip()
    return head


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "__none__")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main(args: Any) -> None:
    import json

    report = build_archived_work_commencement_surface(Path(args.db), args.work_id)
    if args.json:
        print(json.dumps(report.to_jsonable(summary_only=args.summary_only), ensure_ascii=False, indent=2))
        return
    summary = report.summary()
    print(
        f"work_id={summary['work_id']} commencement_witnesses={summary['commencement_witnesses']} "
        f"recorded={summary['recorded']} frontier_residue={summary['frontier_residue']}"
    )
    if summary["refusal_rule_counts"]:
        print(f"refusal_rule_counts={summary['refusal_rule_counts']}")
    if args.summary_only:
        return
    for record in report.records:
        print(
            f"RECORDED\t{record.commencement_date_iso}\t{record.target_address}\t"
            f"subject={record.subject_text!r}\tby={record.commencing_instrument or '-'}"
        )
    for refusal in report.refusals:
        print(f"FRONTIER\t{refusal.commencement_date_iso or '-'}\t{refusal.rule_id}\t{refusal.row_id}")
