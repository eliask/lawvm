"""CompileFacade — public top-level aggregate over the semantic bundle and finding ledger.

This is the stable public dossier surface for core compiler products. It aggregates:

  Semantic plane  → bundle (CanonicalBundle)
  Epistemic plane → finding_ledger (Finding)
  Dossier plane   → optional verdict when already computed

and derives policy/convenience views (has_blocking) without
storing redundant derived state; broader projection helpers live alongside the
shared compile-result read models.

Design rules
------------
* CompileFacade must aggregate the semantic and epistemic planes; it must NOT
  replace them. All primary payloads stay typed and queryable.
* Strictness is DERIVED from findings — not stored as a separate flag or
  reason list.
* No recovered_ops split.  Recovery provenance lives in observations/trace.
* No adjudications list.  Adjudications are a presentation concern; the primary
  surface is the finding ledger, with wrapper views projected from the ledger.
* replay_mode and strict_profile_name capture context for traceability.
* The removed CompileResult envelope is no longer a live API; callers should
  consume this facade directly instead of projecting back out.

Relation to existing types
--------------------------
PhaseResult    — internal stage boundary contract (unchanged)
CompileFacade  — this module — final public dossier built from PhaseResult(s)

The naming diverges from historical "CompileResult" terminology.

API tier
--------
Stable public dossier surface. Reporting/storage consumers should prefer this
module over ``compile_result`` for top-level compile products.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal, Optional, Tuple

from lawvm.contracts import ArtifactEnvelope, ProcessingStatus, to_wire_jsonable
from lawvm.core.compile_result import (
    CanonicalBundle,
    strict_fail_reasons_from_findings_and_verdict,
)
from lawvm.core.phase_result import Finding, PhaseResult

if TYPE_CHECKING:
    from lawvm.core.authority import BranchContext
    from lawvm.core.compile_result import CompileVerdict
    from lawvm.core.ir import IRStatute, ProvisionTimeline, ProvisionVersion, LegalAddress
    from lawvm.core.timeline_results import MaterializationResult, TimelineCompilationResult


def _finding_sort_key(finding: Finding) -> tuple:
    """Canonical ordering key for persisted/projected findings."""
    return (
        finding.role,
        finding.kind,
        finding.stage,
        finding.source_statute,
        bool(finding.blocking),
        tuple(sorted((str(k), repr(v)) for k, v in finding.detail.items())),
    )


# ---------------------------------------------------------------------------
# CompileFacade
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompileFacade:
    """Public top-level aggregate of a completed compilation.

    This is the consumer-facing result object.  Tools, tests, and strict-path
    analysis should consume this rather than reaching into raw PhaseResult
    internals.

    Fields
    ------
        bundle
        The authoritative semantic bundle. Structural operations live in
        ``bundle.structural_ops``; temporal authority lives in
        ``bundle.temporal_events``; lineage authority lives in
        ``bundle.migration_events``.

    finding_ledger
        Stored internal runtime findings. Wrapper-style ``observations`` and
        ``obligations`` are projected by shared read-model helpers over this
        ledger, not stored as parallel authority rails.

    verdict
        Optional strict verdict when the caller has already computed one. If
        present, it is the authoritative strictness summary for the facade.

    replay_mode
        The materialization axis used.  Frontends own the vocabulary: Finland
        uses ``"finlex_oracle"`` and ``"legal_pit"``; other jurisdictions use
        whatever non-empty string their frontend declares.  Core does not
        interpret or validate the mode string — each frontend is responsible
        for validating its own mode values before constructing CompileFacade.
        (§1.5: frontend/kernel boundary — mode semantics belong to the
        frontend, not the shared kernel.)

    strict_profile_name
        The name of the StrictProfile used, if one was provided.  None means
        no explicit profile was applied.
    """

    bundle: CanonicalBundle
    finding_ledger: Tuple[Finding, ...]
    replay_mode: str
    verdict: Optional["CompileVerdict"] = None
    strict_profile_name: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.replay_mode:
            raise ValueError(
                "CompileFacade.replay_mode must be a non-empty string. "
                "Frontends own mode semantics; validate the value before construction."
            )
        if self.verdict is not None and self.strict_profile_name:
            if self.verdict.profile != self.strict_profile_name:
                raise ValueError(
                    "CompileFacade verdict.profile must match strict_profile_name "
                    "when both are provided"
                )
        canonical_findings = tuple(sorted(self.finding_ledger, key=_finding_sort_key))
        if canonical_findings != self.finding_ledger:
            object.__setattr__(self, "finding_ledger", canonical_findings)
        finding_keys = {
            (
                finding.kind,
                finding.role,
                finding.stage,
                finding.source_statute,
                finding.blocking,
                tuple(sorted((str(k), repr(v)) for k, v in finding.detail.items())),
            )
            for finding in self.finding_ledger
        }
        if len(finding_keys) != len(self.finding_ledger):
            raise ValueError("CompileFacade.finding_ledger must not contain duplicate findings")

    # ------------------------------------------------------------------
    # Epistemic convenience views
    # ------------------------------------------------------------------

    @property
    def has_blocking(self) -> bool:
        """True if any obligation is blocking.

        A blocking obligation means downstream output should be treated as
        invalid under a strict profile until the obligation is resolved or
        explicitly allowed by a permissive profile.
        """
        return any(finding.blocking for finding in self.finding_ledger)

    @property
    def migration_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct migration-event kinds carried by this facade."""
        return self.bundle.migration_event_kinds

    @property
    def temporal_event_kinds(self) -> tuple[str, ...]:
        """Return the distinct temporal-event kinds carried by this facade."""
        return self.bundle.temporal_event_kinds

    @property
    def temporal_events_with_activation_rules(self) -> int:
        """Return the number of temporal events carrying an embedded activation rule."""
        return self.bundle.temporal_events_with_activation_rules

    @property
    def temporal_events_with_source(self) -> int:
        """Return the number of temporal events carrying provenance source data."""
        return self.bundle.temporal_events_with_source

    @property
    def temporal_event_activation_rule_kinds(self) -> tuple[str, ...]:
        """Return the distinct activation-rule kinds carried by this facade."""
        return self.bundle.temporal_event_activation_rule_kinds

    def compile_timelines_ex(
        self,
        base: "IRStatute",
        *,
        base_date: str = "",
        label_norm: Optional[Callable[[str], str]] = None,
        authority_context: "BranchContext | None" = None,
    ) -> "TimelineCompilationResult":
        """Compile timelines and preserve typed timeline issues on the facade."""
        from lawvm.core.authority import DEFAULT_ENACTED_CONTEXT  # noqa: PLC0415
        from lawvm.core.timeline import compile_timelines_ex  # noqa: PLC0415

        return compile_timelines_ex(
            base,
            list(self.bundle.structural_ops),
            base_date=base_date,
            label_norm=label_norm,
            temporal_events=self.bundle.temporal_events,
            authority_context=authority_context or DEFAULT_ENACTED_CONTEXT,
        )

    def compile_timeline_findings(
        self,
        base: "IRStatute",
        *,
        base_date: str = "",
        label_norm: Optional[Callable[[str], str]] = None,
    ) -> tuple[Finding, ...]:
        """Project timeline compilation issues into governed findings.

        This is an explicit report/tool-boundary projection. It does not mutate
        ``finding_ledger`` because timeline execution is a query over the bundle,
        not part of the stored compile dossier.
        """
        from lawvm.core.timeline_results import timeline_issues_to_findings  # noqa: PLC0415

        return timeline_issues_to_findings(
            self.compile_timelines_ex(
                base,
                base_date=base_date,
                label_norm=label_norm,
            ).issues
        )

    def materialize_pit_ex(
        self,
        base: "IRStatute",
        as_of: str,
        *,
        base_date: str = "",
        label_norm: Optional[Callable[[str], str]] = None,
        query_type: Literal["governing", "in_force"] = "governing",
        territory: Optional[str] = None,
    ) -> "MaterializationResult":
        """Materialize a PIT view from the authoritative bundle.

        This keeps structural and temporal execution on the façade boundary:
        timelines are compiled from the bundle, then materialized through the
        authoritative explicit-result PIT API.
        """
        from lawvm.core.timeline import materialize_pit_ex  # noqa: PLC0415

        compiled = self.compile_timelines_ex(
            base,
            base_date=base_date,
            label_norm=label_norm,
        )
        from lawvm.core.timeline_results import MaterializationResult  # noqa: PLC0415

        result = materialize_pit_ex(
            compiled.timelines,
            as_of,
            base=base,
            query_type=query_type,
            territory=territory,
            label_norm=label_norm,
        )
        if compiled.issues:
            combined_issues = compiled.issues + result.issues
            status = result.status
            statute = result.statute
            if status == "materialized" and any(issue.blocking for issue in combined_issues):
                from lawvm.core.ir import IRStatute  # noqa: PLC0415

                status = "degraded_timeline_issues"
                metadata = dict(statute.metadata)
                metadata["materialization_status"] = status
                metadata["timeline_issue_rule_ids"] = tuple(
                    issue.rule_id for issue in combined_issues if issue.blocking
                )
                statute = IRStatute(
                    statute_id=statute.statute_id,
                    title=statute.title,
                    body=statute.body,
                    supplements=statute.supplements,
                    metadata=metadata,
                )
            return MaterializationResult(
                status=status,
                statute=statute,
                required_dimensions=result.required_dimensions,
                ambiguous_addresses=result.ambiguous_addresses,
                issues=combined_issues,
                certificate=result.certificate,
            )
        return result

    def materialize_pit_findings(
        self,
        base: "IRStatute",
        as_of: str,
        *,
        base_date: str = "",
        label_norm: Optional[Callable[[str], str]] = None,
        query_type: Literal["governing", "in_force"] = "governing",
        territory: Optional[str] = None,
    ) -> tuple[Finding, ...]:
        """Project PIT materialization issues into governed findings."""
        from lawvm.core.timeline_results import timeline_issues_to_findings  # noqa: PLC0415

        return timeline_issues_to_findings(
            self.materialize_pit_ex(
                base,
                as_of,
                base_date=base_date,
                label_norm=label_norm,
                query_type=query_type,
                territory=territory,
            ).issues
        )

    def provision_lineage(
        self,
        timelines: dict["LegalAddress", "ProvisionTimeline"],
        address: "LegalAddress",
        *,
        as_of_date: str = "",
    ) -> list["ProvisionVersion"]:
        """Return lineage using the bundle's emitted migration chain."""
        return self.bundle.provision_lineage(
            timelines,
            address,
            as_of_date=as_of_date,
        )

    def to_wire_artifact(
        self,
        *,
        producer: str = "lawvm.core.compile_facade",
        version: str = "1",
    ) -> ArtifactEnvelope[dict[str, object]]:
        """Wrap the facade wire projection in a versioned artifact envelope."""
        verdict_payload: Optional[dict[str, object]] = None
        if self.verdict is not None:
            verdict_payload = {
                "mode": self.verdict.mode,
                "profile": self.verdict.profile,
                "status": self.verdict.status,
                "barrier_kinds": self.verdict.barrier_codes,
            }

        findings_payload = tuple(
            {
                "kind": finding.kind,
                "role": finding.role,
                "stage": finding.stage,
                "source_statute": finding.source_statute,
                "blocking": finding.blocking,
                "detail": to_wire_jsonable(dict(finding.detail)),
            }
            for finding in sorted(self.finding_ledger, key=_finding_sort_key)
        )
        status = ProcessingStatus(kind="complete")
        if self.has_blocking:
            blockers = strict_fail_reasons_from_findings_and_verdict(
                self.finding_ledger,
                verdict=self.verdict,
            )
            if not blockers and self.verdict is not None and self.verdict.status != "strict_clean":
                blockers = (self.verdict.status,)
            status = ProcessingStatus(
                kind="partial",
                blockers=blockers,
            )
        return ArtifactEnvelope(
            schema="lawvm.compile_facade",
            producer=producer,
            version=version,
            payload={
                "replay_mode": self.replay_mode,
                "strict_profile_name": self.strict_profile_name,
                "bundle": {
                    "source_statute": self.bundle.source_statute,
                    "target_statute": self.bundle.target_statute,
                    "structural_ops_count": len(self.bundle.structural_ops),
                    "temporal_events_count": len(self.bundle.temporal_events),
                    "temporal_event_kinds": self.temporal_event_kinds,
                    "temporal_events_with_activation_rules": self.temporal_events_with_activation_rules,
                    "temporal_events_with_source": self.temporal_events_with_source,
                    "temporal_event_activation_rule_kinds": self.temporal_event_activation_rule_kinds,
                    "migration_events_count": len(self.bundle.migration_events),
                    "migration_event_kinds": self.migration_event_kinds,
                    "effects_count": len(self.bundle.effects),
                    "groups_count": len(self.bundle.groups),
                    "has_source": self.bundle.source is not None,
                },
                "findings": findings_payload,
                "verdict": verdict_payload,
            },
            status=status,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_phase_result(
        cls,
        pr: PhaseResult,
        replay_mode: str,
        *,
        strict_profile_name: Optional[str] = None,
        verdict: Optional["CompileVerdict"] = None,
    ) -> "CompileFacade":
        """Build a CompileFacade from a top-level PhaseResult.

        The PhaseResult's ``output`` must be either:
          * a canonical ``CanonicalBundle`` carrying structural ops and
            executable temporal events, or
          * ``None`` when the phase result carries only findings/temporal
            residue and no semantic bundle yet.

        Raw iterables of operations are rejected at the shared facade boundary.
        Frontends must lower into ``CanonicalBundle`` before building a public
        ``CompileFacade``.

        Args:
            pr: The top-level PhaseResult from the compilation pipeline.
            replay_mode: The materialization axis (``"finlex_oracle"`` or
                ``"legal_pit"``).
            strict_profile_name: Optional name of the StrictProfile applied
                during compilation.  Stored for traceability only.

        Returns:
            A frozen CompileFacade instance.
        """
        if isinstance(pr.output, CanonicalBundle):
            # pr.output is already a fully-populated CanonicalBundle — use it
            # directly so that source_statute, target_statute, migration_events,
            # effects, groups, and source are preserved through the facade.
            if pr.migration_events:
                raise TypeError(
                    "CompileFacade.from_phase_result requires PhaseResult.migration_events "
                    "to be empty when pr.output is already a CanonicalBundle; "
                    "the canonical bundle owns migration events"
                )
            if pr.temporal_events:
                raise TypeError(
                    "CompileFacade.from_phase_result requires PhaseResult.temporal_events "
                    "to be empty when pr.output is already a CanonicalBundle; "
                    "the canonical bundle owns temporal events"
                )
            bundle = pr.output
        elif pr.output is None:
            bundle = CanonicalBundle(
                temporal_events=pr.temporal_events,
                migration_events=pr.migration_events,
            )
        else:
            raise TypeError(
                "CompileFacade.from_phase_result requires pr.output to be "
                "CanonicalBundle or None; raw operation iterables are no "
                "longer admitted at the shared facade boundary"
            )
        return cls(
            bundle=bundle,
            finding_ledger=pr.findings(),
            verdict=verdict,
            replay_mode=replay_mode,
            strict_profile_name=strict_profile_name,
        )
