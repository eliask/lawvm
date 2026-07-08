"""Export fi_refs.parquet — profile-aware ReferenceMention projection (Slice 3).

Produces fi_refs__{profile}.parquet by running extract_all_reference_mentions
over each statute, then optionally applying INLINE_STATUTE_RESOLUTION claims
to fill NULL target_statute_id_str slots (for non-deterministic-only profiles).

Profile behavior (§6 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2):

  deterministic_only (default):
    - Deterministic extractor rows only. No claims consumed.
    - Output: fi_refs__deterministic_only.parquet + legacy fi_refs.parquet (mirror).
    - Row columns source_witness_type=finlex_akn, claim_id=None, etc.

  strict_with_attested_claims:
    - Deterministic rows + INLINE_STATUTE_RESOLUTION claims that are
      verified_manual (provenance-attested manual review) + span/entailment
      validated.
    - NULL target_statute_id_str slots filled from accepted claims.
    - AmbiguousClaimSet findings emitted; ambiguous rows NOT emitted.

  non_strict_with_claims:
    - As strict but entailment-verified LLM proposals admissible without
      manual verification.

§14 adversary finding (LOAD-BEARING):
  - Output filename encodes profile: fi_refs__{profile}.parquet.
  - Parquet metadata key lawvm.claim_profile is REQUIRED on every output file.
  - Consumers refusing to read without the metadata key are operating correctly.

Schema additions (Slice 3):
  source_witness_type: string
  claim_id:            string (nullable)
  validator_status:    string
  review_status:       string
  replay_authorized:   bool
  emit_profile:        string
"""
from __future__ import annotations

import importlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

from lawvm.core.reference_mention import reference_mention_to_row
from lawvm.core.manual_claims.primitive import (
    ClaimStatus,
    _ProfileTagDeprecated as ProfileTag,
    SourceWitnessType,
    ValidatorStatus,
    ReviewStatus,
)

if TYPE_CHECKING:
    from lawvm.finland.legal_surface.projection import ReferenceSuccessorProjectionRow
    from lawvm.finland.references.registries.statute_name import StatuteNameRegistry
    from lawvm.finland.references.resolve import StatuteSuccessorEdge


class FinlexRepealedByCandidateStatus(StrEnum):
    """Closed status set for Finlex repealedBy candidate evidence rows."""

    CANDIDATE = "candidate"


class FinlexRepealedByPromotionStatus(StrEnum):
    """Closed promotion status set for Finlex repealedBy candidate rows."""

    NOT_PROMOTED = "not_promoted"


@dataclass(frozen=True, slots=True)
class FinlexRepealedByCandidateProjectionRow:
    """Evidence-plane row for Finlex ``repealedBy`` successor candidates.

    These rows record metadata witnesses only. They are intentionally separate
    from :class:`StatuteSuccessorEdge` and never carry replay authority.
    """

    predecessor_work_id: str
    repealing_work_id: str
    effective_from: str
    witness_href: str | None
    witness_text: str
    rule_id: str
    candidate_status: FinlexRepealedByCandidateStatus = (
        FinlexRepealedByCandidateStatus.CANDIDATE
    )
    promotion_status: FinlexRepealedByPromotionStatus = (
        FinlexRepealedByPromotionStatus.NOT_PROMOTED
    )
    replay_authorized: bool = False

    def __post_init__(self) -> None:
        """Keep the candidate-ledger waist typed; JSON lowering is explicit."""
        if not isinstance(self.candidate_status, FinlexRepealedByCandidateStatus):
            raise TypeError(
                "FinlexRepealedByCandidateProjectionRow.candidate_status must be "
                "FinlexRepealedByCandidateStatus"
            )
        if not isinstance(self.promotion_status, FinlexRepealedByPromotionStatus):
            raise TypeError(
                "FinlexRepealedByCandidateProjectionRow.promotion_status must be "
                "FinlexRepealedByPromotionStatus"
            )


class ReferenceSuccessorPromotionRejectionCode(StrEnum):
    """Closed reason-code set for successor-candidate promotion receipts."""

    CANDIDATE_ALREADY_CLAIMS_REPLAY_AUTHORITY = (
        "candidate_already_claims_replay_authority"
    )
    MISSING_PROMOTION_CLAIM = "missing_promotion_claim"
    AMBIGUOUS_PROMOTION_CLAIM = "ambiguous_promotion_claim"
    PROMOTION_CLAIM_NOT_ACCEPTED = "promotion_claim_not_accepted"
    PROMOTION_CLAIM_NOT_VERIFIED_MANUAL = "promotion_claim_not_verified_manual"
    PROMOTION_CLAIM_NOT_MIGRATION_REVALIDATED = (
        "promotion_claim_not_migration_revalidated"
    )
    CANDIDATE_RULE_MISMATCH = "candidate_rule_mismatch"
    INVALID_CANDIDATE_EFFECTIVE_FROM = "invalid_candidate_effective_from"
    PROMOTION_CLAIM_WITHOUT_CANDIDATE = "promotion_claim_without_candidate"


class ReferenceSuccessorFrontierReasonCode(StrEnum):
    """Closed reason-code set for successor-reference frontier rows."""

    MISSING_PROMOTED_SUCCESSOR_CLAIM = "missing_promoted_successor_claim"


@dataclass(frozen=True, slots=True)
class ReferenceSuccessorPromotionClaim:
    """Explicit review/adjudication claim authorizing one candidate edge."""

    predecessor_work_id: str
    repealing_work_id: str
    effective_from: str
    candidate_rule_id: str
    promotion_witness_id: str
    promotion_witness_text: str
    claim_status: ClaimStatus
    review_status: ReviewStatus
    validator_status: ValidatorStatus
    promotion_rule_id: str = "fi.reference_successor.promoted_repealed_by_candidate"


@dataclass(frozen=True, slots=True)
class RejectedRepealedByCandidatePromotion:
    """Visible receipt for a candidate that did not become a successor edge."""

    candidate: FinlexRepealedByCandidateProjectionRow
    reason_code: ReferenceSuccessorPromotionRejectionCode
    reason: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class RejectedReferenceSuccessorPromotionClaim:
    """Visible receipt for a promotion claim that matched no candidate."""

    claim: ReferenceSuccessorPromotionClaim
    reason_code: ReferenceSuccessorPromotionRejectionCode
    reason: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class RepealedByCandidatePromotionResult:
    """Promotion gate output: accepted edges plus rejected input receipts."""

    accepted_edges: tuple["StatuteSuccessorEdge", ...]
    rejected_candidates: tuple[RejectedRepealedByCandidatePromotion, ...]
    rejected_claims: tuple[RejectedReferenceSuccessorPromotionClaim, ...]


@dataclass(frozen=True, slots=True)
class ReferenceSuccessorFrontierRow:
    """Evidence-plane row for literal citations blocked on successor promotion.

    The row says only: this source reference literally cites a work that has
    non-authorizing ``repealedBy`` candidate evidence, but no promoted successor
    edge was accepted for that predecessor in this export slice. It never carries
    an operative work id and never claims replay/query authority.
    """

    source_work_id: str
    source_provision_ref_str: str
    source_span_file: str
    source_span_byte_offset: int | None
    source_span_len: int | None
    surface_text: str
    literal_work_id: str
    successor_as_of: str
    candidate_repealing_work_ids: tuple[str, ...]
    candidate_effective_from: tuple[str, ...]
    candidate_rule_ids: tuple[str, ...]
    candidate_promotion_rejection_codes: tuple[
        ReferenceSuccessorPromotionRejectionCode, ...
    ] = ()
    reason_code: ReferenceSuccessorFrontierReasonCode = (
        ReferenceSuccessorFrontierReasonCode.MISSING_PROMOTED_SUCCESSOR_CLAIM
    )
    replay_authorized: bool = False


# ---------------------------------------------------------------------------
# Profile metadata helpers
# ---------------------------------------------------------------------------


def _profile_filename(base_name: str, profile: ProfileTag) -> str:
    """Return the profile-stamped filename stem."""
    return f"{base_name}__{profile.value}"


def _attach_profile_metadata(table: Any, profile: ProfileTag) -> Any:
    """Attach lawvm.claim_profile metadata key to a pyarrow Table."""
    existing = table.schema.metadata or {}
    meta = dict(existing)
    meta[b"lawvm.claim_profile"] = profile.value.encode()
    return table.replace_schema_metadata(meta)


# ---------------------------------------------------------------------------
# Deterministic row helpers
# ---------------------------------------------------------------------------


# Authority firewall (AGENTS.md §1.11/§2.10, contract §7, legal_surface_graph D7):
# deterministic extraction is a SURFACE projection — surface_only by construction.
# A deterministic row records WHERE a reference was extracted from; it carries NO
# replay/review authority. So these columns must hold surface-truthful values that
# do NOT claim a human verified the row or that replay is authorized:
#   - replay_authorized = False   (no execution authority; the legal_surface_graph
#                                  plane already enforces this and RAISES on a True)
#   - review_status     = PROPOSED   (machine-produced, NOT verified_manual)
#   - validator_status  = UNVALIDATED (no span/entailment manual validation)
# The decision-driven NULL-slot-fill path (_apply_null_slot_fills) legitimately
# overwrites these from the composer-derived ClaimCompositionDecision; only the
# author-set deterministic default lived here, and it was an authority leak.
# `deterministic_extraction` is the positive surface fact this row actually carries.
_DETERMINISTIC_ROW_EXTRAS = {
    "source_witness_type": SourceWitnessType.FINLEX_AKN.value,
    "claim_id": None,
    "validator_status": ValidatorStatus.UNVALIDATED.value,
    "review_status": ReviewStatus.PROPOSED.value,
    "replay_authorized": False,
    "deterministic_extraction": True,
}


def _augment_row(row: Dict[str, Any], profile: ProfileTag, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add Slice 3 columns to a projection row."""
    result = dict(row)
    if extras:
        result.update(extras)
    else:
        result.update(_DETERMINISTIC_ROW_EXTRAS)
    result["emit_profile"] = profile.value
    return result


# ---------------------------------------------------------------------------
# Corpus + XML loading
# ---------------------------------------------------------------------------


def _load_corpus_store() -> Any:
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    try:
        return store.read_oracle(statute_id)
    except Exception:
        return None


def _required_edge_str(row: Dict[str, Any], key: str, *, line_no: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(
            "load_reference_successor_edges: "
            f"line {line_no} field {key!r} must be a non-empty string"
        )
    return value


def _required_claim_str(row: Dict[str, Any], key: str, *, line_no: int) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(
            "load_reference_successor_promotion_claims: "
            f"line {line_no} field {key!r} must be a non-empty string"
        )
    return value


def _required_claim_status(row: Dict[str, Any], *, line_no: int) -> ClaimStatus:
    raw = _required_claim_str(row, "claim_status", line_no=line_no)
    try:
        return ClaimStatus(raw)
    except ValueError as exc:
        raise ValueError(
            "load_reference_successor_promotion_claims: "
            f"line {line_no} field 'claim_status' is not a known ClaimStatus"
        ) from exc


def _required_review_status(row: Dict[str, Any], *, line_no: int) -> ReviewStatus:
    raw = _required_claim_str(row, "review_status", line_no=line_no)
    try:
        return ReviewStatus(raw)
    except ValueError as exc:
        raise ValueError(
            "load_reference_successor_promotion_claims: "
            f"line {line_no} field 'review_status' is not a known ReviewStatus"
        ) from exc


def _required_validator_status(
    row: Dict[str, Any], *, line_no: int
) -> ValidatorStatus:
    raw = _required_claim_str(row, "validator_status", line_no=line_no)
    try:
        return ValidatorStatus(raw)
    except ValueError as exc:
        raise ValueError(
            "load_reference_successor_promotion_claims: "
            f"line {line_no} field 'validator_status' is not a known "
            "ValidatorStatus"
        ) from exc


def load_reference_successor_edges(path: str | Path) -> list["StatuteSuccessorEdge"]:
    """Load explicit witnessed successor edges from JSONL.

    This is an input boundary, not an acquisition heuristic. Each row must carry
    the exact :class:`StatuteSuccessorEdge` fields; the loader does not infer a
    successor from title equality, repeal date, or current citation state.
    """
    from lawvm.finland.references.resolve import StatuteSuccessorEdge

    edges: list[StatuteSuccessorEdge] = []
    edge_path = Path(path)
    for line_no, raw in enumerate(edge_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "load_reference_successor_edges: "
                f"line {line_no} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict):
            raise ValueError(
                "load_reference_successor_edges: "
                f"line {line_no} must be a JSON object"
            )
        raw_effective = _required_edge_str(obj, "effective_from", line_no=line_no)
        try:
            effective_from = date.fromisoformat(raw_effective)
        except ValueError as exc:
            raise ValueError(
                "load_reference_successor_edges: "
                f"line {line_no} field 'effective_from' must be an ISO date"
            ) from exc
        edges.append(
            StatuteSuccessorEdge(
                predecessor_work_id=_required_edge_str(
                    obj, "predecessor_work_id", line_no=line_no
                ),
                successor_work_id=_required_edge_str(
                    obj, "successor_work_id", line_no=line_no
                ),
                effective_from=effective_from,
                witness_id=_required_edge_str(obj, "witness_id", line_no=line_no),
                witness_text=_required_edge_str(obj, "witness_text", line_no=line_no),
                rule_id=(
                    _required_edge_str(obj, "rule_id", line_no=line_no)
                    if "rule_id" in obj
                    else "fi.reference_successor.witnessed_edge"
                ),
            )
        )
    return edges


def load_reference_successor_promotion_claims(
    path: str | Path,
) -> list[ReferenceSuccessorPromotionClaim]:
    """Load explicit reviewed promotion claims from JSONL.

    This loader does not read Finlex metadata and does not mint successor edges.
    It only parses the adjudication input consumed by
    :func:`promote_repealed_by_candidates_to_successor_edges`.
    """
    claims: list[ReferenceSuccessorPromotionClaim] = []
    claim_path = Path(path)
    for line_no, raw in enumerate(claim_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "load_reference_successor_promotion_claims: "
                f"line {line_no} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict):
            raise ValueError(
                "load_reference_successor_promotion_claims: "
                f"line {line_no} must be a JSON object"
            )
        raw_effective = _required_claim_str(obj, "effective_from", line_no=line_no)
        try:
            date.fromisoformat(raw_effective)
        except ValueError as exc:
            raise ValueError(
                "load_reference_successor_promotion_claims: "
                f"line {line_no} field 'effective_from' must be an ISO date"
            ) from exc
        claims.append(
            ReferenceSuccessorPromotionClaim(
                predecessor_work_id=_required_claim_str(
                    obj, "predecessor_work_id", line_no=line_no
                ),
                repealing_work_id=_required_claim_str(
                    obj, "repealing_work_id", line_no=line_no
                ),
                effective_from=raw_effective,
                candidate_rule_id=_required_claim_str(
                    obj, "candidate_rule_id", line_no=line_no
                ),
                promotion_witness_id=_required_claim_str(
                    obj, "promotion_witness_id", line_no=line_no
                ),
                promotion_witness_text=_required_claim_str(
                    obj, "promotion_witness_text", line_no=line_no
                ),
                claim_status=_required_claim_status(obj, line_no=line_no),
                review_status=_required_review_status(obj, line_no=line_no),
                validator_status=_required_validator_status(obj, line_no=line_no),
                promotion_rule_id=(
                    _required_claim_str(obj, "promotion_rule_id", line_no=line_no)
                    if "promotion_rule_id" in obj
                    else "fi.reference_successor.promoted_repealed_by_candidate"
                ),
            )
        )
    return claims


def _promotion_key(
    predecessor_work_id: str,
    repealing_work_id: str,
    effective_from: str,
) -> tuple[str, str, str]:
    return (predecessor_work_id, repealing_work_id, effective_from)


def promote_repealed_by_candidates_to_successor_edges(
    candidates: Sequence[FinlexRepealedByCandidateProjectionRow],
    promotion_claims: Sequence[ReferenceSuccessorPromotionClaim],
) -> RepealedByCandidatePromotionResult:
    """Promote reviewed Finlex candidates to successor edges.

    ``FinlexRepealedByCandidate`` proves only that Finlex exposes repeal
    metadata. This gate requires an explicit promotion claim for the exact
    predecessor/repealing/date tuple and returns receipts for every candidate it
    refuses. The output edges are suitable for the existing explicit
    ``StatuteSuccessorEdge`` input boundary; rejected candidates remain evidence
    and carry no replay authority.
    """
    from lawvm.finland.references.resolve import StatuteSuccessorEdge

    claims_by_key: dict[tuple[str, str, str], list[int]] = {}
    for claim_index, claim in enumerate(promotion_claims):
        claims_by_key.setdefault(
            _promotion_key(
                claim.predecessor_work_id,
                claim.repealing_work_id,
                claim.effective_from,
            ),
            [],
        ).append(claim_index)

    accepted_edges: list[StatuteSuccessorEdge] = []
    rejected: list[RejectedRepealedByCandidatePromotion] = []
    consumed_claim_indexes: set[int] = set()
    for candidate in candidates:
        key = _promotion_key(
            candidate.predecessor_work_id,
            candidate.repealing_work_id,
            candidate.effective_from,
        )
        matching_claim_indexes = claims_by_key.get(key, [])
        consumed_claim_indexes.update(matching_claim_indexes)
        if candidate.replay_authorized:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .CANDIDATE_ALREADY_CLAIMS_REPLAY_AUTHORITY
                    ),
                    reason=(
                        "Finlex repealedBy candidates must arrive as evidence "
                        "with replay_authorized=false before promotion."
                    ),
                )
            )
            continue
        if not matching_claim_indexes:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .MISSING_PROMOTION_CLAIM
                    ),
                    reason=(
                        "No explicit promotion claim matched this "
                        "predecessor/repealing/effective_from candidate."
                    ),
                )
            )
            continue
        if len(matching_claim_indexes) > 1:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .AMBIGUOUS_PROMOTION_CLAIM
                    ),
                    reason=(
                        "More than one promotion claim matched this candidate; "
                        "successor authority is ambiguous."
                    ),
                )
            )
            continue
        claim = promotion_claims[matching_claim_indexes[0]]
        if claim.claim_status is not ClaimStatus.ACCEPTED:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .PROMOTION_CLAIM_NOT_ACCEPTED
                    ),
                    reason=(
                        "Promotion claim claim_status must be 'accepted' "
                        "before it can mint a successor edge."
                    ),
                )
            )
            continue
        if claim.review_status is not ReviewStatus.VERIFIED_MANUAL:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .PROMOTION_CLAIM_NOT_VERIFIED_MANUAL
                    ),
                    reason=(
                        "Promotion claim review_status must be "
                        "'verified_manual' before it can mint a successor edge."
                    ),
                )
            )
            continue
        if claim.validator_status is not ValidatorStatus.MIGRATION_REVALIDATED:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .PROMOTION_CLAIM_NOT_MIGRATION_REVALIDATED
                    ),
                    reason=(
                        "Promotion claim validator_status must be "
                        "'migration_revalidated' before it can mint a "
                        "successor edge."
                    ),
                )
            )
            continue
        if claim.candidate_rule_id != candidate.rule_id:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .CANDIDATE_RULE_MISMATCH
                    ),
                    reason=(
                        "Promotion claim did not cite the candidate rule id "
                        "that produced this witness."
                    ),
                )
            )
            continue
        try:
            effective_from = date.fromisoformat(candidate.effective_from)
        except ValueError:
            rejected.append(
                RejectedRepealedByCandidatePromotion(
                    candidate=candidate,
                    reason_code=(
                        ReferenceSuccessorPromotionRejectionCode
                        .INVALID_CANDIDATE_EFFECTIVE_FROM
                    ),
                    reason="Candidate effective_from is not an ISO date.",
                )
            )
            continue
        accepted_edges.append(
            StatuteSuccessorEdge(
                predecessor_work_id=candidate.predecessor_work_id,
                successor_work_id=candidate.repealing_work_id,
                effective_from=effective_from,
                witness_id=claim.promotion_witness_id,
                witness_text=claim.promotion_witness_text,
                rule_id=claim.promotion_rule_id,
            )
        )

    rejected_claims = [
        RejectedReferenceSuccessorPromotionClaim(
            claim=claim,
            reason_code=(
                ReferenceSuccessorPromotionRejectionCode
                .PROMOTION_CLAIM_WITHOUT_CANDIDATE
            ),
            reason=(
                "Promotion claim did not match any Finlex repealedBy "
                "candidate in the corpus slice."
            ),
        )
        for claim_index, claim in enumerate(promotion_claims)
        if claim_index not in consumed_claim_indexes
    ]

    return RepealedByCandidatePromotionResult(
        accepted_edges=tuple(accepted_edges),
        rejected_candidates=tuple(rejected),
        rejected_claims=tuple(rejected_claims),
    )


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------


def _project_refs_for_statute(
    statute_id: str,
    store: Any,
    profile: ProfileTag,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project ReferenceMention rows for one statute.

    SOURCE OF TRUTH (Pro r5 Phase 3 Stage 3): dispatches to the Legal Surface
    Graph projector by DEFAULT. The graph is the single source of truth for the
    fi_refs export; the extractor path is retained (as
    :func:`_project_refs_for_statute_via_extractor`) purely as the PARITY ORACLE
    that the parity gate (``tests/test_fi_export_parity.py``) compares against.

    Returns (mention_rows, diagnostic_rows) with profile columns attached.
    """
    return _project_refs_for_statute_via_graph(statute_id, store, profile)


def _project_refs_for_statute_via_extractor(
    statute_id: str,
    store: Any,
    profile: ProfileTag,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """PARITY ORACLE: project rows via the deterministic ReferenceMention extractor.

    This is the historical export path. It is NO LONGER the default writer (the
    graph projector is), but it is kept reachable so the parity gate can assert
    the graph path reproduces it field-for-field. The extractor surfaces
    diagnostics; the graph projector cannot (diagnostics are not graph nodes), so
    only this path returns diagnostic rows.

    Returns (mention_rows, diagnostic_rows) with profile columns attached.
    """
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions
    from lawvm.finland.references.elliptical_resolve import (
        resolve_elliptical_mentions,
    )

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_all_reference_mentions(xml_bytes, statute_id)

    # Match the graph path (ReferenceLens): resolve elliptical INTERNAL refs
    # (bare momentti / bare kohta) against the statute tree before projecting.
    # The pass is cardinality-preserving (one resolution per input mention) and
    # downgrades an un-anchorable bare ref to OPEN rather than leaving the
    # recognizer's raw EXACT on a target that resolves only to the statute root.
    mentions = [
        res.mention
        for res in resolve_elliptical_mentions(list(result.mentions), xml_bytes)
    ]

    mention_rows = [
        _augment_row(reference_mention_to_row(m), profile)
        for m in mentions
    ]

    diag_rows: List[Dict[str, Any]] = []
    for diag in result.diagnostics:
        diag_rows.append({
            "statute_id": statute_id,
            "rule_id": diag.rule_id,
            "family": diag.family,
            "phase": diag.phase,
            "reason": diag.reason,
            "blocking": diag.blocking,
        })

    return mention_rows, diag_rows


def _project_refs_for_statute_via_graph(
    statute_id: str,
    store: Any,
    profile: ProfileTag,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """SOURCE OF TRUTH: project rows via the Legal Surface Graph.

    Reads xml_bytes -> builds the Legal Surface Graph -> reconstructs
    ReferenceMention records from the graph (the Phase-3b parity-proven
    reconstruction) -> runs them through the SAME ``reference_mention_to_row``
    projection + ``_augment_row`` the extractor path uses. Full-row parity with
    the extractor is proven by ``tests/test_fi_export_parity.py``.

    PERFORMANCE: the export only needs reference rows, so we build the graph with
    ONLY the ReferenceLens (``lenses=(ReferenceLens(),)``) and no cross-lens edge
    passes (``edge_passes=()`` — the default DefinitionClosurePass operates on
    definition nodes, which a reference-only build does not mint). This keeps the
    fi_refs export as fast as the extractor path while leaving the projection
    unaffected: ``graph_to_reference_mentions`` only reads ``reference_expr`` /
    ``reference_resolution`` nodes + the intrinsic ``resolution_of`` edge, all of
    which the ReferenceLens alone produces.

    Returns (mention_rows, diagnostic_rows). The graph carries no extractor
    diagnostics, so diag_rows is always empty here (diagnostics remain available
    via :func:`_project_refs_for_statute_via_extractor`).
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
    from lawvm.finland.legal_surface.lenses.references import ReferenceLens
    from lawvm.finland.legal_surface.projection import graph_to_reference_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    graph = build_legal_surface_graph(
        xml_bytes,
        statute_id,
        lenses=(ReferenceLens(),),
        edge_passes=(),
    )
    mentions = graph_to_reference_mentions(graph)

    mention_rows = [
        _augment_row(reference_mention_to_row(m), profile)
        for m in mentions
    ]

    diag_rows: List[Dict[str, Any]] = []
    return mention_rows, diag_rows


def _project_reference_successors_for_statute_via_graph(
    statute_id: str,
    store: Any,
    *,
    successor_edges: Sequence["StatuteSuccessorEdge"],
    successor_as_of: date | str,
    statute_registry: "StatuteNameRegistry | None" = None,
) -> list["ReferenceSuccessorProjectionRow"]:
    """Project dated successor rows for one statute via the Legal Surface Graph.

    This is a sibling projection to ``fi_refs``: it never changes the literal
    citation rows or the legacy parquet schema. ``successor_edges`` are supplied
    typed witness records; absence of a witness yields no operative rewrite.
    When no statute-name registry is supplied, an empty registry is used so
    explicit ``NNN/YYYY`` citations can still be resolved as literals while
    by-name mentions remain unresolved rather than guessed.
    """
    from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
    from lawvm.finland.legal_surface.lenses.references import ReferenceLens
    from lawvm.finland.legal_surface.projection import graph_to_reference_successor_rows
    from lawvm.finland.references.registries.statute_name import StatuteNameRegistry

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return []

    registry = statute_registry if statute_registry is not None else StatuteNameRegistry()
    graph = build_legal_surface_graph(
        xml_bytes,
        statute_id,
        statute_registry=registry,
        successor_edges=tuple(successor_edges),
        successor_as_of=successor_as_of,
        lenses=(ReferenceLens(),),
        edge_passes=(),
    )
    return graph_to_reference_successor_rows(graph)


def _project_repealed_by_candidate_for_statute(
    statute_id: str,
    store: Any,
) -> FinlexRepealedByCandidateProjectionRow | None:
    """Project Finlex ``repealedBy`` metadata as a non-authorizing candidate."""
    from lawvm.finland.references.registries.statute_name import (
        _extract_repealed_by_candidate,
    )

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return None
    candidate = _extract_repealed_by_candidate(xml_bytes)
    if candidate is None:
        return None
    return FinlexRepealedByCandidateProjectionRow(
        predecessor_work_id=statute_id,
        repealing_work_id=candidate.repealing_work_id,
        effective_from=candidate.effective_from.isoformat(),
        witness_href=candidate.witness_href,
        witness_text=candidate.witness_text,
        rule_id=candidate.rule_id,
    )


def _project_refs_for_statute_deterministic(
    statute_id: str,
    store: Any,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """2-arg adapter for the DETERMINISTIC_ONLY profile.

    The parallel corpus mapper expects a ``(statute_id, store)`` projector. For
    the deterministic profile there is no cross-statute post-processing (the
    claim NULL-slot fill only runs for non-deterministic profiles), so per-
    statute projection is fully independent and safe to shard. Module-level so
    it is picklable for the worker pool.
    """
    return _project_refs_for_statute(statute_id, store, ProfileTag.DETERMINISTIC_ONLY)


# ---------------------------------------------------------------------------
# Claim-based NULL-slot fill (Piece 4)
# ---------------------------------------------------------------------------


def _load_accepted_inline_statute_claims(
    claims_base_dir: Path,
) -> List[Any]:
    """Load all accepted INLINE_STATUTE_RESOLUTION claims from storage.

    Returns list of (ManualCompilationClaim, ClaimState) pairs.
    Returns [] if no claims directory or no claims present.
    """
    # Activate Finland claim kinds for registry
    importlib.import_module("lawvm.finland.claim_kinds")

    kind_name = "fi.v1.INLINE_STATUTE_RESOLUTION"
    by_kind_dir = claims_base_dir / "by-kind" / kind_name
    if not by_kind_dir.exists():
        return []

    from lawvm.core.manual_claims.storage import ClaimStore
    from lawvm.core.manual_claims.state import project_state

    store = ClaimStore(claims_base_dir)
    pairs = []
    for claim_id in store.list_claims_by_kind(kind_name):
        claim = store.read_claim(claim_id)
        events = list(store.read_events(claim_id))
        state = project_state(claim_id, events)
        if state is not None and state.claim_state_status == ClaimStatus.ACCEPTED:
            pairs.append((claim, state))
    return pairs


def _apply_null_slot_fills(
    mention_rows: List[Dict[str, Any]],
    accepted_claims: List[Any],
    profile: ProfileTag,
    build_id: str,
    precedence_registry: Any,
    emitted_events: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Fill NULL target_statute_id_str slots from accepted claims.

    For each accepted claim, find matching fi_refs rows by
    (source_statute_id + section_locator + mention_span) and fill NULL
    target_statute_id_str. Returns (updated_rows, ambiguous_findings).

    Slice 3 only handles NULL-slot fill; new-row addition is Slice 4.
    """
    from lawvm.core.manual_claims.composer import derive_composition_decision
    from lawvm.core.manual_claims.precedence import AmbiguousClaimSet

    if not accepted_claims:
        return mention_rows, []

    # Index rows by (statute_id, provision_ref_str, span_start, span_end)
    # The fi_refs row has source_statute_id, source_provision_ref_str,
    # source_span_byte_offset, source_span_len
    def _row_key(row: Dict[str, Any]) -> tuple[object, object, object, object]:
        return (
            row.get("source_statute_id", ""),
            row.get("source_provision_ref_str", ""),
            row.get("source_span_byte_offset"),
            row.get("source_span_len"),
        )

    row_index: Dict[tuple[object, object, object, object], int] = {
        _row_key(r): i for i, r in enumerate(mention_rows)
    }
    updated = list(mention_rows)
    ambiguous_findings: List[AmbiguousClaimSet] = []

    # Group claims by their target key
    # claim.target = tuple of (key, value) pairs:
    #   statute_id, section_locator, mention_span
    for claim, state in accepted_claims:
        target_dict = dict(claim.target)
        value_dict = dict(claim.value)

        statute_id = target_dict.get("statute_id", "")
        section_locator = target_dict.get("section_locator", "")
        mention_span = target_dict.get("mention_span")
        if not isinstance(mention_span, (list, tuple)) or len(mention_span) != 2:
            continue

        span_start = mention_span[0]
        span_len = mention_span[1] - mention_span[0]

        row_key = (statute_id, section_locator, span_start, span_len)
        idx = row_index.get(row_key)
        if idx is None:
            # No matching row — new-row creation deferred to Slice 4
            continue

        row = updated[idx]
        # Only fill if the target_statute_id_str is NULL/empty
        current_target = row.get("target_statute_id", "") or row.get("target_statute_id_str", "")
        if current_target:
            continue  # deterministic has a value; do not override (§3.2: augments-NULL-only)

        # Run composer to get authorization for this claim+profile
        decision, event = derive_composition_decision(
            claim=claim,
            state=state,
            profile=profile,
            build_id=build_id,
            precedence_registry=precedence_registry,
        )
        emitted_events.append(event)

        if not decision.authorized:
            continue

        # Fill the NULL slot
        resolved_id = value_dict.get("resolved_statute_id", "")
        row_copy = dict(row)
        # fi_refs.parquet uses "target_statute_id" as the column name
        if "target_statute_id" in row_copy:
            row_copy["target_statute_id"] = resolved_id
        if "target_statute_id_str" in row_copy:
            row_copy["target_statute_id_str"] = resolved_id
        row_copy["source_witness_type"] = claim.source_witness_type.value
        row_copy["claim_id"] = claim.claim_id
        row_copy["validator_status"] = state.validator_status.value
        row_copy["review_status"] = state.review_status.value
        row_copy["replay_authorized"] = decision.replay_authorized
        row_copy["emit_profile"] = profile.value
        updated[idx] = row_copy

    return updated, ambiguous_findings


# ---------------------------------------------------------------------------
# JSONL + Parquet writers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def _reference_successor_row_to_dict(
    row: "ReferenceSuccessorProjectionRow",
) -> Dict[str, Any]:
    """Serialize the typed successor projection row without changing its shape."""
    return {
        "source_work_id": row.source_work_id,
        "source_provision_ref_str": row.source_provision_ref_str,
        "source_span_file": row.source_span_file,
        "source_span_byte_offset": row.source_span_byte_offset,
        "source_span_len": row.source_span_len,
        "surface_text": row.surface_text,
        "literal_work_id": row.literal_work_id,
        "operative_work_id": row.operative_work_id,
        "successor_as_of": row.successor_as_of,
        "successor_status": row.successor_status.value,
        "successor_resolution_basis": row.successor_resolution_basis.value,
        "successor_candidates": list(row.successor_candidates),
        "successor_rejected_candidates": list(row.successor_rejected_candidates),
        "successor_reason_code": row.successor_reason_code.value,
        "successor_chain": [
            {
                "predecessor_work_id": witness.predecessor_work_id,
                "successor_work_id": witness.successor_work_id,
                "effective_from": witness.effective_from.isoformat(),
                "witness_id": witness.witness_id,
                "witness_text": witness.witness_text,
                "rule_id": witness.rule_id,
            }
            for witness in row.successor_chain
        ],
    }


def _repealed_by_candidate_row_to_dict(
    row: FinlexRepealedByCandidateProjectionRow,
) -> Dict[str, Any]:
    """Serialize a non-authorizing Finlex repealedBy candidate row."""
    return {
        "predecessor_work_id": row.predecessor_work_id,
        "repealing_work_id": row.repealing_work_id,
        "effective_from": row.effective_from,
        "witness_href": row.witness_href,
        "witness_text": row.witness_text,
        "rule_id": row.rule_id,
        "candidate_status": row.candidate_status.value,
        "promotion_status": row.promotion_status.value,
        "replay_authorized": row.replay_authorized,
    }


def _successor_edge_to_dict(row: "StatuteSuccessorEdge") -> Dict[str, Any]:
    """Serialize an accepted successor edge without changing its authority shape."""
    return {
        "predecessor_work_id": row.predecessor_work_id,
        "successor_work_id": row.successor_work_id,
        "effective_from": row.effective_from.isoformat(),
        "witness_id": row.witness_id,
        "witness_text": row.witness_text,
        "rule_id": row.rule_id,
    }


def _promotion_claim_to_dict(row: ReferenceSuccessorPromotionClaim) -> Dict[str, Any]:
    """Serialize a reviewed promotion claim without changing its shape."""
    return {
        "predecessor_work_id": row.predecessor_work_id,
        "repealing_work_id": row.repealing_work_id,
        "effective_from": row.effective_from,
        "candidate_rule_id": row.candidate_rule_id,
        "promotion_witness_id": row.promotion_witness_id,
        "promotion_witness_text": row.promotion_witness_text,
        "claim_status": row.claim_status.value,
        "review_status": row.review_status.value,
        "validator_status": row.validator_status.value,
        "promotion_rule_id": row.promotion_rule_id,
    }


def _rejected_repealed_by_promotion_to_dict(
    rejection: RejectedRepealedByCandidatePromotion,
) -> Dict[str, Any]:
    """Serialize a rejected candidate-promotion receipt."""
    return {
        "candidate": _repealed_by_candidate_row_to_dict(rejection.candidate),
        "reason_code": rejection.reason_code.value,
        "reason": rejection.reason,
        "blocking": rejection.blocking,
    }


def _rejected_promotion_claim_to_dict(
    rejection: RejectedReferenceSuccessorPromotionClaim,
) -> Dict[str, Any]:
    """Serialize a promotion-claim rejection receipt."""
    return {
        "claim": _promotion_claim_to_dict(rejection.claim),
        "reason_code": rejection.reason_code.value,
        "reason": rejection.reason,
        "blocking": rejection.blocking,
    }


def _reference_successor_frontier_row_to_dict(
    row: ReferenceSuccessorFrontierRow,
) -> Dict[str, Any]:
    """Serialize a non-authorizing successor frontier row."""
    return {
        "source_work_id": row.source_work_id,
        "source_provision_ref_str": row.source_provision_ref_str,
        "source_span_file": row.source_span_file,
        "source_span_byte_offset": row.source_span_byte_offset,
        "source_span_len": row.source_span_len,
        "surface_text": row.surface_text,
        "literal_work_id": row.literal_work_id,
        "successor_as_of": row.successor_as_of,
        "candidate_repealing_work_ids": list(row.candidate_repealing_work_ids),
        "candidate_effective_from": list(row.candidate_effective_from),
        "candidate_rule_ids": list(row.candidate_rule_ids),
        "candidate_promotion_rejection_codes": [
            code.value for code in row.candidate_promotion_rejection_codes
        ],
        "reason_code": row.reason_code.value,
        "replay_authorized": row.replay_authorized,
    }


def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    """Attach CompileMetadata fields to a pyarrow Table's schema metadata."""
    if compile_metadata is None:
        raise ValueError(
            "export_fi_refs: CompileMetadata is required for v3 substrate-locked "
            "persistence. Construct via build_default_compile_metadata() or "
            "explicitly. See UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5."
        )
    existing = table.schema.metadata or {}
    meta = dict(existing)
    for k, v in compile_metadata.to_metadata_dict().items():
        meta[k.encode()] = v.encode()
    return table.replace_schema_metadata(meta)


# The CANONICAL fi_refs parquet schema (field name + arrow type), as a list of
# (name, arrow-type-factory) pairs so the explicit pa.schema can be built lazily
# (pyarrow is an optional import) and pinned on BOTH the empty AND the populated
# write paths. Pinning the explicit schema on the populated path is the rank-21
# fix: ``pa.Table.from_pylist(rows)`` INFERS the schema from the dict rows, so a
# column rename / drop / type drift in ``reference_mention_to_row`` or
# ``_augment_row`` would silently produce a DIFFERENT parquet schema (an untyped
# boundary) instead of a loud failure. Passing this schema to ``from_pylist``
# makes any drift a hard ``pyarrow`` error at write (a missing key → the column
# is all-NULL but typed; an EXTRA key not in the schema → from_pylist RAISES;
# a type mismatch → RAISES), so the projection schema is type-carried, not
# convention-bridged. Order/columns mirror ``reference_mention_to_row`` (14 base)
# + ``_DETERMINISTIC_ROW_EXTRAS`` (6 provenance/surface) + ``emit_profile`` (1).
def _fi_refs_arrow_schema(pa: Any) -> Any:
    """Build the explicit, pinned fi_refs parquet schema (requires pyarrow)."""
    return pa.schema([
        pa.field("source_statute_id", pa.string()),
        pa.field("source_provision_ref_str", pa.string()),
        pa.field("target_statute_id", pa.string()),
        pa.field("target_provision_ref_str", pa.string()),
        pa.field("cite_kind", pa.string()),
        pa.field("cite_confidence", pa.string()),
        pa.field("edge_subtype", pa.string()),
        pa.field("phrase_lemma", pa.string()),
        pa.field("source_span_file", pa.string()),
        pa.field("source_span_byte_offset", pa.int64()),
        pa.field("source_span_len", pa.int64()),
        pa.field("valid_at_start", pa.string()),
        pa.field("valid_at_end", pa.string()),
        pa.field("target_stat_hash", pa.string()),
        # Slice 3 provenance columns
        pa.field("source_witness_type", pa.string()),
        pa.field("claim_id", pa.string()),
        pa.field("validator_status", pa.string()),
        pa.field("review_status", pa.string()),
        pa.field("replay_authorized", pa.bool_()),
        # Surface fact: this row was produced by deterministic extraction
        # (carries NO replay/review authority — see _DETERMINISTIC_ROW_EXTRAS).
        pa.field("deterministic_extraction", pa.bool_()),
        pa.field("emit_profile", pa.string()),
    ])


def _reference_successors_arrow_schema(pa: Any) -> Any:
    """Build the explicit, pinned fi_reference_successors parquet schema."""
    return pa.schema([
        pa.field("source_work_id", pa.string()),
        pa.field("source_provision_ref_str", pa.string()),
        pa.field("surface_text", pa.string()),
        pa.field("literal_work_id", pa.string()),
        pa.field("operative_work_id", pa.string()),
        pa.field("successor_as_of", pa.string()),
        pa.field("successor_status", pa.string()),
        pa.field("successor_resolution_basis", pa.string()),
        pa.field("successor_candidates", pa.list_(pa.string())),
        pa.field("successor_rejected_candidates", pa.list_(pa.string())),
        pa.field("successor_reason_code", pa.string()),
        pa.field(
            "successor_chain",
            pa.list_(
                pa.struct([
                    pa.field("predecessor_work_id", pa.string()),
                    pa.field("successor_work_id", pa.string()),
                    pa.field("effective_from", pa.string()),
                    pa.field("witness_id", pa.string()),
                    pa.field("witness_text", pa.string()),
                    pa.field("rule_id", pa.string()),
                ])
            ),
        ),
    ])


def _repealed_by_candidates_arrow_schema(pa: Any) -> Any:
    """Build the explicit, pinned fi_repealed_by_candidates parquet schema."""
    return pa.schema([
        pa.field("predecessor_work_id", pa.string()),
        pa.field("repealing_work_id", pa.string()),
        pa.field("effective_from", pa.string()),
        pa.field("witness_href", pa.string()),
        pa.field("witness_text", pa.string()),
        pa.field("rule_id", pa.string()),
        pa.field("candidate_status", pa.string()),
        pa.field("promotion_status", pa.string()),
        pa.field("replay_authorized", pa.bool_()),
    ])


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    profile: ProfileTag,
    compile_metadata: Any = None,
) -> bool:
    """Try to write rows as Parquet with profile + compile metadata. Returns True if ok."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    schema = _fi_refs_arrow_schema(pa)

    if not rows:
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        table = _attach_profile_metadata(table, profile)
        table = _attach_compile_metadata(table, compile_metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    # Pin the explicit schema (do NOT let from_pylist INFER it from the dicts):
    # a column drift now fails loud instead of silently writing a different
    # schema. NB: ``from_pylist(rows, schema=schema)`` alone is NOT sufficient —
    # pyarrow SILENTLY drops dict keys absent from the schema and SILENTLY NULLs
    # schema columns absent from the dict (only a TYPE mismatch raises). So we
    # first assert the row key-set equals the schema field-set (the loud guard a
    # rename/add/drop trips), then pass the schema so a type drift also raises.
    expected_cols = {f.name for f in schema}
    row_cols = set(rows[0].keys())
    if row_cols != expected_cols:
        missing = expected_cols - row_cols
        extra = row_cols - expected_cols
        raise ValueError(
            "export_fi_refs: parquet row schema drift — projected rows no longer "
            "match the pinned fi_refs schema. "
            f"missing_columns={sorted(missing)} extra_columns={sorted(extra)}. "
            "A column was renamed/added/dropped in reference_mention_to_row / "
            "_augment_row; update _fi_refs_arrow_schema and all consumers in "
            "lockstep (no version-migration burden — we control the whole stack)."
        )
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
    except (pa.lib.ArrowInvalid, pa.lib.ArrowTypeError) as exc:
        raise ValueError(
            "export_fi_refs: parquet column TYPE drift — a value in the projected "
            "rows no longer matches the pinned fi_refs schema type. Update "
            f"_fi_refs_arrow_schema and all consumers in lockstep. Underlying: {exc}"
        ) from exc
    table = _attach_profile_metadata(table, profile)
    table = _attach_compile_metadata(table, compile_metadata)
    pq.write_table(table, str(path), compression="zstd")
    return True


def _try_write_reference_successors_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    compile_metadata: Any = None,
) -> bool:
    """Try to write successor rows as Parquet with pinned schema metadata."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    schema = _reference_successors_arrow_schema(pa)
    expected_cols = {f.name for f in schema}

    if not rows:
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        table = _attach_compile_metadata(table, compile_metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    row_cols = set(rows[0].keys())
    if row_cols != expected_cols:
        missing = expected_cols - row_cols
        extra = row_cols - expected_cols
        raise ValueError(
            "export_fi_reference_successors: parquet row schema drift — "
            "projected rows no longer match the pinned successor-reference "
            f"schema. missing_columns={sorted(missing)} "
            f"extra_columns={sorted(extra)}."
        )
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
    except (pa.lib.ArrowInvalid, pa.lib.ArrowTypeError) as exc:
        raise ValueError(
            "export_fi_reference_successors: parquet column TYPE drift — a "
            "value in the projected rows no longer matches the pinned schema "
            f"type. Underlying: {exc}"
        ) from exc
    table = _attach_compile_metadata(table, compile_metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path), compression="zstd")
    return True


def _try_write_repealed_by_candidates_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    compile_metadata: Any = None,
) -> bool:
    """Try to write Finlex repealedBy candidate rows with pinned schema."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    schema = _repealed_by_candidates_arrow_schema(pa)
    expected_cols = {f.name for f in schema}

    if not rows:
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        table = _attach_compile_metadata(table, compile_metadata)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    row_cols = set(rows[0].keys())
    if row_cols != expected_cols:
        missing = expected_cols - row_cols
        extra = row_cols - expected_cols
        raise ValueError(
            "export_fi_repealed_by_candidates: parquet row schema drift — "
            "projected rows no longer match the pinned candidate schema. "
            f"missing_columns={sorted(missing)} extra_columns={sorted(extra)}."
        )
    try:
        table = pa.Table.from_pylist(rows, schema=schema)
    except (pa.lib.ArrowInvalid, pa.lib.ArrowTypeError) as exc:
        raise ValueError(
            "export_fi_repealed_by_candidates: parquet column TYPE drift — a "
            "value in the projected rows no longer matches the pinned schema "
            f"type. Underlying: {exc}"
        ) from exc
    table = _attach_compile_metadata(table, compile_metadata)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(path), compression="zstd")
    return True


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_fi_refs(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    profile: ProfileTag = ProfileTag.DETERMINISTIC_ONLY,
    build_id: str = "default",
    claims_base_dir: Optional[Path] = None,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Export fi_refs__{profile}.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:           List of (amendment_count, statute_id) tuples.
        data_dir:         Output directory.
        use_parquet:      Write Parquet if pyarrow available (also writes JSONL).
        limit:            Process only first N statutes (for testing).
        profile:          Which claim composition profile to use.
        build_id:         Stable build identifier for composition events.
        claims_base_dir:  Path to manual_claims/ directory. Defaults to
                          {data_dir}/manual_claims.

    Returns:
        Number of ReferenceMention rows written.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    if claims_base_dir is None:
        claims_base_dir = out / "manual_claims"

    # Load precedence registry (required for non-deterministic profiles)
    precedence_registry = None
    if profile != ProfileTag.DETERMINISTIC_ONLY:
        from lawvm.core.manual_claims.precedence import load_precedence_registry
        yaml_path = out / "claim_precedence.yaml"
        if not yaml_path.exists():
            # Try the standard location one level up
            yaml_path = out.parent / "claim_precedence.yaml"
        if not yaml_path.exists():
            print(
                f"  warning: claim_precedence.yaml not found at {yaml_path}; "
                "proceeding without precedence resolution (ambiguous sets will be skipped)",
                file=sys.stderr,
            )
            precedence_registry = None
        else:
            precedence_registry = load_precedence_registry(yaml_path)

    total = len(corpus)
    all_mention_rows: List[Dict[str, Any]] = []
    all_diag_rows: List[Dict[str, Any]] = []

    if profile == ProfileTag.DETERMINISTIC_ONLY:
        # No cross-statute post-processing: shard per statute and reassemble in
        # corpus order (byte-identical to the serial accumulation).
        from lawvm.tools._parallel_corpus import project_corpus_parallel

        statute_ids = [sid for _, sid in corpus]
        all_mention_rows, all_diag_rows = project_corpus_parallel(
            statute_ids=statute_ids,
            projector_ref=(__name__, "_project_refs_for_statute_deterministic"),
            serial_projector=_project_refs_for_statute_deterministic,
            store=store,
            workers=workers,
        )
        print(f"  refs: {len(all_mention_rows):,} mention rows over {total:,} statutes")
    else:
        # Non-deterministic profiles run claim NULL-slot fills below that read
        # accumulated rows; keep the serial loop so that path is unchanged.
        for i, (_, statute_id) in enumerate(corpus, 1):
            t0 = time.time()
            mention_rows, diag_rows = _project_refs_for_statute(statute_id, store, profile)
            all_mention_rows.extend(mention_rows)
            all_diag_rows.extend(diag_rows)

            if i % 50 == 0 or i == total:
                elapsed = time.time() - t0
                print(f"  [{i}/{total}] refs: {len(all_mention_rows):,} total ({elapsed:.1f}s last)")

    # For non-deterministic profiles, apply NULL-slot fills from claims
    composition_events: List[Any] = []
    consumed_claim_ids: List[str] = []
    accepted_claims: List[Any] = []
    if profile != ProfileTag.DETERMINISTIC_ONLY and precedence_registry is not None:
        accepted_claims = _load_accepted_inline_statute_claims(claims_base_dir)
        # Slice 5: strict-mode guard — refuse retracted claims
        _check_no_retracted_claims_in_strict(accepted_claims, profile)
        if accepted_claims:
            all_mention_rows, _ = _apply_null_slot_fills(
                all_mention_rows,
                accepted_claims,
                profile,
                build_id,
                precedence_registry,
                composition_events,
            )
            # Track which claims were consumed (those that actually filled a slot)
            for row in all_mention_rows:
                cid = row.get("claim_id")
                if cid and cid not in consumed_claim_ids:
                    consumed_claim_ids.append(cid)

    # Write outputs
    profile_stem = _profile_filename("fi_refs", profile)

    # Always write JSONL
    jsonl_count = _write_jsonl(out / f"{profile_stem}.jsonl", all_mention_rows)

    parquet_path_str = str(out / f"{profile_stem}.parquet")
    if use_parquet:
        # Write profile-stamped parquet (canonical)
        ok = _try_write_parquet(out / f"{profile_stem}.parquet", all_mention_rows, profile, compile_metadata)
        if ok:
            print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (Parquet + JSONL)")
            # Mirror as legacy fi_refs.parquet for deterministic_only profile only
            if profile == ProfileTag.DETERMINISTIC_ONLY:
                _try_write_parquet(out / "fi_refs.parquet", all_mention_rows, profile, compile_metadata)
                _write_jsonl(out / "fi_refs.jsonl", all_mention_rows)
        else:
            print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (JSONL only; pyarrow not installed)")
    else:
        print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (JSONL)")

    # Slice 5: emit consumed events for each claim used in this build
    if consumed_claim_ids and claims_base_dir is not None:
        claim_rows = [r for r in all_mention_rows if r.get("claim_id")]
        track_consumption_for_build(
            build_id=build_id,
            profile=profile,
            projection_artifact_path=parquet_path_str,
            consumed_claim_ids=consumed_claim_ids,
            affected_projection_rows=claim_rows,
            claims_base_dir=claims_base_dir,
        )

    # Write diagnostics
    if all_diag_rows:
        _write_jsonl(out / "fi_refs_diagnostics.jsonl", all_diag_rows)

    return jsonl_count


def export_fi_reference_successors(
    corpus: List[Tuple[int, str]],
    *,
    successor_edges: Sequence["StatuteSuccessorEdge"],
    successor_as_of: date | str,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    statute_registry: "StatuteNameRegistry | None" = None,
    compile_metadata: Optional[Any] = None,
) -> int:
    """Export dated successor-reference projection rows for a Finnish corpus.

    This writes ``fi_reference_successors.jsonl`` and, when available,
    ``fi_reference_successors.parquet``. It is a separate artifact from
    ``fi_refs``: literal citations remain literal, and this table carries only
    the dated operative-endpoint projection authorized by typed
    ``StatuteSuccessorEdge`` witnesses supplied by the caller.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = len(corpus)
    all_successor_rows: List[Dict[str, Any]] = []
    for i, (_, statute_id) in enumerate(corpus, 1):
        rows = _project_reference_successors_for_statute_via_graph(
            statute_id,
            store,
            successor_edges=successor_edges,
            successor_as_of=successor_as_of,
            statute_registry=statute_registry,
        )
        all_successor_rows.extend(
            _reference_successor_row_to_dict(row) for row in rows
        )
        if i % 50 == 0 or i == total:
            print(
                "  "
                f"[{i}/{total}] fi_reference_successors: "
                f"{len(all_successor_rows):,} total"
            )

    jsonl_count = _write_jsonl(
        out / "fi_reference_successors.jsonl", all_successor_rows
    )
    if use_parquet:
        ok = _try_write_reference_successors_parquet(
            out / "fi_reference_successors.parquet",
            all_successor_rows,
            compile_metadata,
        )
        if ok:
            print(
                "  "
                f"fi_reference_successors: {jsonl_count:,} rows "
                "(Parquet + JSONL)"
            )
        else:
            print(
                "  "
                f"fi_reference_successors: {jsonl_count:,} rows "
                "(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  fi_reference_successors: {jsonl_count:,} rows (JSONL)")

    return jsonl_count


def _project_repealed_by_candidates_for_corpus(
    corpus: List[Tuple[int, str]],
    store: Any,
) -> list[FinlexRepealedByCandidateProjectionRow]:
    rows: list[FinlexRepealedByCandidateProjectionRow] = []
    for _, statute_id in corpus:
        row = _project_repealed_by_candidate_for_statute(statute_id, store)
        if row is not None:
            rows.append(row)
    return rows


def _row_literal_work_id(row: Mapping[str, Any]) -> str | None:
    """Return the literal target work id from a fi_refs-style row, if present."""
    value = row.get("target_statute_id")
    if not value:
        value = row.get("target_statute_id_str")
    return str(value) if value else None


def _optional_int_row_value(row: Mapping[str, Any], key: str) -> int | None:
    """Return an optional integer row value from a projection-boundary row."""
    value = row.get(key)
    return int(value) if value is not None else None


def project_reference_successor_frontier_rows(
    mention_rows: Sequence[Mapping[str, Any]],
    *,
    candidates: Sequence[FinlexRepealedByCandidateProjectionRow],
    accepted_edges: Sequence["StatuteSuccessorEdge"],
    candidate_rejections: Sequence[RejectedRepealedByCandidatePromotion],
    successor_as_of: date | str,
) -> tuple[ReferenceSuccessorFrontierRow, ...]:
    """Project literal references blocked on missing successor promotion claims.

    Candidate rows are evidence only. An accepted edge for a predecessor removes
    that predecessor from this missing-promotion frontier; the ordinary
    ``fi_reference_successors`` artifact will own those resolved references.
    """
    accepted_predecessors = {
        edge.predecessor_work_id for edge in accepted_edges
    }
    candidates_by_predecessor: dict[str, list[FinlexRepealedByCandidateProjectionRow]] = {}
    for candidate in candidates:
        if candidate.predecessor_work_id in accepted_predecessors:
            continue
        candidates_by_predecessor.setdefault(
            candidate.predecessor_work_id, []
        ).append(candidate)
    rejection_codes_by_candidate_key: dict[
        tuple[str, str, str, str], list[ReferenceSuccessorPromotionRejectionCode]
    ] = {}
    for rejection in candidate_rejections:
        candidate = rejection.candidate
        rejection_codes_by_candidate_key.setdefault(
            (
                candidate.predecessor_work_id,
                candidate.repealing_work_id,
                candidate.effective_from,
                candidate.rule_id,
            ),
            [],
        ).append(rejection.reason_code)

    rows: list[ReferenceSuccessorFrontierRow] = []
    as_of_text = (
        successor_as_of.isoformat()
        if isinstance(successor_as_of, date)
        else str(successor_as_of)
    )
    for row in mention_rows:
        literal_work_id = _row_literal_work_id(row)
        if literal_work_id is None:
            continue
        predecessor_candidates = candidates_by_predecessor.get(literal_work_id)
        if not predecessor_candidates:
            continue
        source_work_id = str(row.get("source_statute_id") or "")
        source_provision_ref_str = str(row.get("source_provision_ref_str") or "")
        source_span_file = str(row.get("source_span_file") or "")
        surface_text = str(row.get("surface_text") or row.get("phrase_lemma") or "")
        rows.append(
            ReferenceSuccessorFrontierRow(
                source_work_id=source_work_id,
                source_provision_ref_str=source_provision_ref_str,
                source_span_file=source_span_file,
                source_span_byte_offset=_optional_int_row_value(
                    row, "source_span_byte_offset"
                ),
                source_span_len=_optional_int_row_value(row, "source_span_len"),
                surface_text=surface_text,
                literal_work_id=literal_work_id,
                successor_as_of=as_of_text,
                candidate_repealing_work_ids=tuple(
                    dict.fromkeys(c.repealing_work_id for c in predecessor_candidates)
                ),
                candidate_effective_from=tuple(
                    dict.fromkeys(c.effective_from for c in predecessor_candidates)
                ),
                candidate_rule_ids=tuple(
                    dict.fromkeys(c.rule_id for c in predecessor_candidates)
                ),
                candidate_promotion_rejection_codes=tuple(
                    dict.fromkeys(
                        code
                        for c in predecessor_candidates
                        for code in rejection_codes_by_candidate_key.get(
                            (
                                c.predecessor_work_id,
                                c.repealing_work_id,
                                c.effective_from,
                                c.rule_id,
                            ),
                            [],
                        )
                    )
                ),
            )
        )
    return tuple(rows)


def export_fi_repealed_by_candidates(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    compile_metadata: Optional[Any] = None,
) -> int:
    """Export Finlex ``repealedBy`` candidate witness rows for a Finnish corpus.

    This writes ``fi_repealed_by_candidates.jsonl`` and, when available,
    ``fi_repealed_by_candidates.parquet``. The rows are evidence-plane
    candidates only: they record Finlex metadata that may later support a
    promoted ``StatuteSuccessorEdge``, but this function never emits such an
    edge and every row carries ``replay_authorized=false``.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    total = len(corpus)
    all_candidate_rows: List[Dict[str, Any]] = []
    for i, (_, statute_id) in enumerate(corpus, 1):
        row = _project_repealed_by_candidate_for_statute(statute_id, store)
        if row is not None:
            all_candidate_rows.append(_repealed_by_candidate_row_to_dict(row))
        if i % 50 == 0 or i == total:
            print(
                "  "
                f"[{i}/{total}] fi_repealed_by_candidates: "
                f"{len(all_candidate_rows):,} total"
            )

    jsonl_count = _write_jsonl(
        out / "fi_repealed_by_candidates.jsonl", all_candidate_rows
    )
    if use_parquet:
        ok = _try_write_repealed_by_candidates_parquet(
            out / "fi_repealed_by_candidates.parquet",
            all_candidate_rows,
            compile_metadata,
        )
        if ok:
            print(
                "  "
                f"fi_repealed_by_candidates: {jsonl_count:,} rows "
                "(Parquet + JSONL)"
            )
        else:
            print(
                "  "
                f"fi_repealed_by_candidates: {jsonl_count:,} rows "
                "(JSONL only; pyarrow not installed)"
            )
    else:
        print(f"  fi_repealed_by_candidates: {jsonl_count:,} rows (JSONL)")

    return jsonl_count


def export_fi_reference_successors_from_promoted_candidates(
    corpus: List[Tuple[int, str]],
    *,
    promotion_claims: Sequence[ReferenceSuccessorPromotionClaim],
    successor_as_of: date | str,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    statute_registry: "StatuteNameRegistry | None" = None,
    compile_metadata: Optional[Any] = None,
) -> int:
    """Export successor rows using only explicitly promoted Finlex candidates.

    The function scans corpus ``repealedBy`` candidate witnesses, applies the
    explicit promotion gate, writes rejected-promotion receipts, and then calls
    the normal successor-reference projection with the accepted
    ``StatuteSuccessorEdge`` rows. Candidate metadata that lacks a matching
    promotion claim never becomes replay/query authority.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    candidates = _project_repealed_by_candidates_for_corpus(corpus, store)
    promotion = promote_repealed_by_candidates_to_successor_edges(
        candidates, promotion_claims
    )
    all_mention_rows: list[dict[str, Any]] = []
    for _, statute_id in corpus:
        mention_rows, _diag_rows = _project_refs_for_statute(
            statute_id, store, ProfileTag.DETERMINISTIC_ONLY
        )
        all_mention_rows.extend(mention_rows)
    frontier_rows = project_reference_successor_frontier_rows(
        all_mention_rows,
        candidates=candidates,
        accepted_edges=promotion.accepted_edges,
        candidate_rejections=promotion.rejected_candidates,
        successor_as_of=successor_as_of,
    )
    _write_jsonl(
        out / "fi_reference_successor_edges_from_promotions.jsonl",
        [_successor_edge_to_dict(edge) for edge in promotion.accepted_edges],
    )
    _write_jsonl(
        out / "fi_repealed_by_candidate_promotion_rejections.jsonl",
        [
            _rejected_repealed_by_promotion_to_dict(rejection)
            for rejection in promotion.rejected_candidates
        ],
    )
    _write_jsonl(
        out / "fi_reference_successor_promotion_claim_rejections.jsonl",
        [
            _rejected_promotion_claim_to_dict(rejection)
            for rejection in promotion.rejected_claims
        ],
    )
    _write_jsonl(
        out / "fi_reference_successor_frontier.jsonl",
        [
            _reference_successor_frontier_row_to_dict(row)
            for row in frontier_rows
        ],
    )

    return export_fi_reference_successors(
        corpus,
        successor_edges=promotion.accepted_edges,
        successor_as_of=successor_as_of,
        data_dir=data_dir,
        use_parquet=use_parquet,
        limit=None,
        statute_registry=statute_registry,
        compile_metadata=compile_metadata,
    )


# ---------------------------------------------------------------------------
# Slice 5: consumption tracking + strict-mode retraction guard
# ---------------------------------------------------------------------------


def _hash_row(row: dict[str, Any]) -> str:
    """Stable hash for a projection row (for taint-report row tracking)."""
    import hashlib
    serialized = json.dumps(
        {k: str(v) for k, v in sorted(row.items()) if k != "emit_profile"},
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()[:32]


def track_consumption_for_build(
    build_id: str,
    profile: ProfileTag,
    projection_artifact_path: str,
    consumed_claim_ids: List[str],
    affected_projection_rows: List[Dict[str, Any]],
    claims_base_dir: Path,
) -> None:
    """Emit a ClaimStateEvent(event_kind='consumed') for each consumed claim.

    Per §5.1 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2.
    Records build_id, profile, row_hashes, and invalidated_PIT_intervals in
    the event reason payload (JSON).

    Called by export_fi_refs after the parquet write completes.
    """
    from lawvm.core.manual_claims.storage import ClaimStore
    from lawvm.core.manual_claims.primitive import (
        ClaimStateEvent,
        Producer,
    )
    from datetime import datetime, timezone

    if not consumed_claim_ids:
        return

    store = ClaimStore(claims_base_dir)
    row_hashes = [_hash_row(r) for r in affected_projection_rows]

    now = datetime.now(tz=timezone.utc)
    producer = Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=now,
        environment="lawvm-export-fi-refs",
    )

    for claim_id in consumed_claim_ids:
        if not store.claim_exists(claim_id):
            continue

        claim = store.read_claim(claim_id)
        valid_at = claim.valid_at

        reason_payload = json.dumps({
            "build_id": build_id,
            "profile": profile.value,
            "projection_artifact_path": projection_artifact_path,
            "row_hashes": row_hashes,
            "invalidated_PIT_intervals": [
                {
                    "target_locator": (
                        claim.claim_scope.provision_ref or claim.claim_scope.statute_id
                    ),
                    "interval_start": valid_at[0].isoformat(),
                    "interval_end": valid_at[1].isoformat() if valid_at[1] else None,
                }
            ],
            "dependent_downstream_artifacts": [],
        })

        event = ClaimStateEvent(
            claim_id=claim_id,
            event_kind="consumed",
            timestamp=now,
            producer=producer,
            old_status=None,
            new_status=None,
            reason=reason_payload,
        )
        store.append_event(event)


def _check_no_retracted_claims_in_strict(
    accepted_claims: List[Any],
    profile: ProfileTag,
) -> None:
    """For strict profile: raise if any claim to be consumed is retracted.

    Per §5.4: strict-mode builds refuse to incorporate retracted claims.
    Operator must rebuild (new build_id = clean by construction).
    """
    if profile != ProfileTag.STRICT_WITH_ATTESTED_CLAIMS:
        return

    from lawvm.core.manual_claims.primitive import ClaimStatus

    for claim, state in accepted_claims:
        if state.claim_state_status == ClaimStatus.RETRACTED:
            raise SystemExit(
                f"error: strict profile refuses retracted claim {claim.claim_id[:32]}... "
                "Retract the claim and rebuild to produce a clean artifact."
            )
