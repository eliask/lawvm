"""§2.9 production-lane guard-liveness for the UK citation-graph totality probe (D6 / §A7).

First UK consumer of the jurisdiction-neutral
``lawvm.core.citation_graph_totality_audit.assert_citation_graph_totality``
(registry row ``REFERENCE.UNCLASSIFIED_REFERENCE``, the §0 surface-totality
enforcement: every emitted ``ReferenceMention`` MUST carry a typed
classification or surface as a typed Observation — never silently dropped from
the citation graph).

The core audit is jurisdiction-neutral and consumes the shared reference
carriers (``reference_mention.py``) verbatim. The UK frontend does not yet
extract inline cross-statute citations into typed ``ReferenceMention`` carriers,
so the UK reference surface at fold-exit is currently empty
(``_uk_reference_mentions_from_statute`` returns ``()``). This probe is the
wire-in regardless: it surfaces whatever UK reference mentions exist, feeds the
audit, and emits a non-blocking ``CompileAdjudication`` per unclassified
mention. Wired so the totality guard is live the moment UK gains a citation
extractor — and so the discipline is disclosed now, not deferred.

Built on the shared ``lawvm.uk_legislation.probe_base`` harness per §2.6:
module-scope ``ProbeSpec`` + ``make_probe_observed_adjudication`` /
``make_probe_skip_adjudication`` / ``probe_env_enabled``. Default-off behind
``LAWVM_UK_CITATION_GRAPH_TOTALITY_PROBE`` so production UK bench replay output
stays byte-stable until a deliberate ramp.
"""
from __future__ import annotations

from typing import Optional, Sequence

from lawvm.core.citation_graph_totality_audit import (
    REFERENCE_UNCLASSIFIED_REFERENCE,
    assert_citation_graph_totality,
)
from lawvm.core.phase_result import Observation
from lawvm.core.reference_mention import ReferenceMention, ReferenceResolution
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.probe_base import (
    ProbeSpec,
    detail_mapping_to_json_safe_dict,
    make_probe_observed_adjudication,
    make_probe_skip_adjudication,
    probe_env_enabled,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_reference_mentions_from_statute,
)

UK_CITATION_GRAPH_TOTALITY_KIND = "uk_replay_citation_graph_totality_observed"

_PROBE_SPEC = ProbeSpec(
    env_flag="LAWVM_UK_CITATION_GRAPH_TOTALITY_PROBE",
    kind=UK_CITATION_GRAPH_TOTALITY_KIND,
    skipped_kind="uk_replay_citation_graph_totality_probe_skipped",
    family="citation_graph_totality",
    audit_module_path=(
        "core.citation_graph_totality_audit.assert_citation_graph_totality + "
        "lawvm.uk_legislation.witness_builders._uk_reference_mentions_from_statute"
    ),
    witness_prior_art=(
        "d6_citation_graph_totality_reference_mention_surface_wire"
    ),
    core_registry_finding_kind=REFERENCE_UNCLASSIFIED_REFERENCE,
)


def probe_uk_citation_graph_totality(
    replayed: object,
    *,
    mentions: Optional[Sequence[ReferenceMention]] = None,
    resolutions: Sequence[ReferenceResolution] = (),
    adjudications_out: Optional[list[CompileAdjudication]] = None,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """Run the citation-graph totality probe, appending a non-blocking
    ``CompileAdjudication`` per emitted ``ReferenceMention`` that lacks a typed
    classification.

    Args:
        replayed: the UK replayed ``IRStatute`` at fold-exit. The UK reference
            surface is projected from it via
            ``_uk_reference_mentions_from_statute`` (currently empty — see that
            builder's seam docstring).
        mentions: optional explicit mention stream. When ``None`` (the
            production fold-exit case) the UK surface is projected from
            ``replayed``. Tests pass an explicit stream to drive a firing case
            through this exact production code path.
        resolutions: the set-level ``ReferenceResolution`` receipts. A
            finding-requiring mention is covered when a resolution over its
            surface is present.
        adjudications_out: optional sink for the per-finding adjudications.
        source_statute: the base statute id of the surface under audit.

    Returns the typed Observations (also surfaced as CompileAdjudications on
    ``adjudications_out`` when non-empty). Emits nothing when every emitted
    mention carries a typed classification — and nothing at all while the UK
    reference surface is empty.
    """
    if not probe_env_enabled(_PROBE_SPEC.env_flag):
        return ()
    statute_id = str(source_statute or "")
    try:
        if mentions is None:
            surfaced = _uk_reference_mentions_from_statute(replayed)
            # Narrow the witness-builder's ``object`` surface back to the typed
            # carrier; the builder returns ``ReferenceMention`` instances (empty
            # tuple today). A non-ReferenceMention here is a seam-contract break
            # and fails loud into the probe-skip lane below.
            mention_seq: tuple[ReferenceMention, ...] = tuple(
                m for m in surfaced if isinstance(m, ReferenceMention)
            )
        else:
            mention_seq = tuple(mentions)
        observations = assert_citation_graph_totality(
            mention_seq,
            resolutions,
            source_statute=statute_id,
        )
    except Exception as exc:  # noqa: BLE001 — fail-loud-as-no-op, never strict
        if adjudications_out is not None:
            adjudications_out.append(
                make_probe_skip_adjudication(
                    _PROBE_SPEC,
                    statute_id=statute_id,
                    reason=(
                        f"probe_unexpected_error: "
                        f"{exc.__class__.__name__}: {exc!r}"
                    ),
                )
            )
        return ()
    if not observations:
        return ()
    for observation in observations:
        obs_detail = detail_mapping_to_json_safe_dict(observation.detail)
        obs_detail["audit_finding_kind"] = observation.kind
        obs_detail["audit_stage"] = observation.stage
        adjudication = make_probe_observed_adjudication(
            _PROBE_SPEC,
            statute_id=statute_id,
            message=(
                "UK replay fold exit: an emitted ReferenceMention reached the "
                "citation surface without a typed classification — a §0 "
                "totality short fall. The audit never resolves the reference, "
                "re-tags its confidence, or drops the mention; strict "
                "enforcement stays multi-session pending a UK strict_profile lane."
            ),
            extra_detail={
                "reason_code": "unclassified_reference_observed",
                "audit_finding_kind": observation.kind,
                "audit_stage": observation.stage,
                "observation_detail": obs_detail,
            },
        )
        if adjudications_out is not None:
            adjudications_out.append(adjudication)
    return observations


__all__ = [
    "UK_CITATION_GRAPH_TOTALITY_KIND",
    "probe_uk_citation_graph_totality",
]
