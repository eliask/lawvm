"""Sweden (SE) agreement residual projector — typed evidence-plane residuals.

Sibling of :mod:`lawvm.finland.agreement_residual_proof_projector`. The replay
row ``classification`` string in ``check_se_official_replay``'s dict-shaped
return is a **PROJECTION-row** (§2.10 projection plane), so the dict shape is
NOT a §1.9 violation there — projections may be dict-shaped and re-derived.
The architecture gap is that the SE frontend emits NO typed evidence-plane
account of the residual classification whose ``residual_id`` is content-
addressed and survives a re-run for the same (sfs_id, label, classification,
text-prefix) tuple. This module fills that gap: per replay row, emit a typed
:class:`~lawvm.core.agreement_residual.AgreementResidual` carrying:

* a content-addressed ``residual_id`` (stable across reruns, distinguishing
  each (amending + base + section + classification) tuple);
* a closed-vocabulary ``AgreementResidualFamily`` mapping the SE classification
  set into the shared 13-family enum (so the SE residual ledger participates
  in the cross-jurisdiction substrate);
* an ``agreement_residual_status`` distinguishing agreement / frontier /
  residual (only ``frontier`` and ``residual`` are *unresolved* — agreement
  rows are provable, replay-authorized for projection);
* the witness ``rule_id`` whose classification produced the row;
* a non-empty ``missing_proofs`` tuple for unresolved rows, so a checker
  knows what each open residual asks of a future fix.

Per §0 / AGENTS.md §2.10: these residuals are DESCRIBED — they explain
projection authority; they do not BECOME authority. They live in the
evidence plane; their ``residual_id`` hash is the only hash they carry; they
never enter a semantic object's identity. The CLI/aggregate dict emits them
as a re-derivable ``"agreement_residuals"`` list, so a projection view is
re-consiliable from a committed dossier (§2.10 projection-plane rule).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, cast

from lawvm.core.agreement_residual import AgreementResidual

_SE_JURISDICTION = "se"
_SE_AGREEMENT_SURFACE = "se_official_replay"


# The closed world of SE replay-row ``classification`` strings emitted by
# ``check_se_official_replay``. Each maps to exactly one
# ``AgreementResidualFamily`` + ``AgreementResidualStatus``. Asserted closed
# by tests/test_se_agreement_residuals::test_classification_family_map_closed
# against the live classification strings in fetch.py.
#
# Each tuple is ``(family, status, safe_default, missing_proofs)``: the
# per-classification rule_id is uniform — ``se_replay_classification_to_agreement_residual``
# — the projector's single owned evidence-plane witness (cataloged in the SE
# believed_spec). The distinct *classification* string travels in
# ``detail["classification"]`` for downstream consumers; the family is what
# the cross-jurisdiction substrate types over.
_SE_CLASSIFICATION_FAMILY_TABLE: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    # classification → (family, status, safe_default, missing_proofs)
    #
    # ### agreement (correct, provable) ###
    "exact": (
        "agreement",
        "agrees",
        "record_as_genuine_match",
        (),
    ),
    "table_rows_match": (
        "agreement",
        "agrees",
        "record_as_genuine_match",
        (),
    ),
    # ### editorial projection match (match=True via presentation cleanup) ###
    # Replay text and oracle text agree modulo editorial presentation: a
    # §2.1 historical_tolerance / presentation_cleanup family projection folds
    # them. Family is oracle_side editorial pathology (the oracle stamps inline
    # list numerators / provenance suffixes replay omitted), NOT a replay bug.
    "editorial_attribution_only": (
        "oracle_editorial_pathology",
        "agrees",
        "fold_via_presentation_normalization",
        (),
    ),
    "inline_numbering_only": (
        "oracle_editorial_pathology",
        "agrees",
        "fold_via_presentation_normalization",
        (),
    ),
    # ### temporal mismatch (oracle is a different-date consolidation) ###
    # Per §0 the dominant residual family: replay is correct, the SFS
    # consolidated oracle is the LATEST consolidation only and a strictly-
    # later consolidation stamp carries later-amendment text. These are typed
    # frontier residuals (NOT replay bugs). The registered SE declared
    # assumption ``se_data_ceiling_single_version_oracle`` owns this class.
    "official_oracle_version_mismatch": (
        "temporal_mismatch",
        "frontier",
        "reframe_as_oracle_version_mismatch_bucket",
        (
            "point_in_time_consolidation_snapshot",
            "forward_older_base_rebuild_for_chain",
        ),
    ),
    # REPEAL by this amendment, then a strictly-later amendment reinstated the
    # section with different text. The replay produces the empty post-D
    # section (correct); the oracle shows the later-readded text. Same family
    # as temporal_mismatch — the oracle carries a post-D time-point.
    "repeal_then_later_replaced_oracle_only": (
        "temporal_mismatch",
        "frontier",
        "reframe_as_oracle_version_mismatch_bucket",
        ("point_in_time_consolidation_snapshot",),
    ),
    # ### unknown (oracle is unusable as the agreement surface) ###
    # Oracle's own consolidation stamp is missing or non-comparable; the
    # replay-vs-oracle comparison could not classify against a dateable
    # time-point. Status=frontier (an honest "we cannot classify this row").
    "official_oracle_match_missing_current_post": (
        "unknown",
        "frontier",
        "skip_in_genuine_mismatch_denominator",
        ("archive_current_text_for_base",),
    ),
    "official_oracle_match_version_unknown": (
        "unknown",
        "frontier",
        "skip_in_genuine_mismatch_denominator",
        ("oracle_consolidation_stamp_parse",),
    ),
    # ### oracle editorial pathology (oracle carries a tombstone for a repealed
    # ### section that replay left structurally empty). Match=True via the
    # ### "Har upphävts genom..." stub fold rule — the oracle's tombstone is
    # ### editorial, not substantive text divergence.
    "repeal_stub_oracle_only": (
        "oracle_editorial_pathology",
        "agrees",
        "fold_oracle_repeal_stub_against_empty_replay",
        (),
    ),
    # ### replay_bug (the genuine §0 3-way classification frontier) ###
    # ``official_oracle_match_current_surface_drift`` is the row class the §0
    # doctrine splits three ways: deterministic gap (LawVM wrong — fix it),
    # manual-compilation frontier (the source doesn't deterministically
    # specify the result — needs an owned claim), or oracle-suspect (LawVM is
    # right, official text is stale — a finding). Without further
    # discrimination here it stays in ``replay_bug`` family with status=
    # residual — a checker can pick up its ``residual_id`` to triage.
    "official_oracle_match_current_surface_drift": (
        "replay_bug",
        "residual",
        "triage_deterministic_gap_or_manual_compilation_or_oracle_suspect",
        (
            "audit_against_official_act_provisions",
            "check_later_chain_for_non_invertible_blockers",
            "oracle_text_quality_review",
        ),
    ),
    # ### genuine mismatch (replay text diverges from correctly-dated oracle) ###
    # The agent only lands ``official_oracle_match_current_surface_drift`` as
    # a current-surface row; if a future classifier emits ``genuine_mismatch``
    # directly, route it through the same replay_bug family (defensive —
    # not currently produced).
    "genuine_mismatch": (
        "replay_bug",
        "residual",
        "triage_deterministic_gap_or_oracle_suspect",
        ("audit_against_official_act_provisions", "oracle_text_quality_review"),
    ),
}


# All distinct classification strings produced by ``check_se_official_replay``
# as of the 2026-06 hardening pass. Closed set — tests assert this is at parity
# with the live emit sites in src/lawvm/sweden/fetch.py.
SE_REPLAY_ROW_CLASSIFICATIONS: frozenset[str] = frozenset(_SE_CLASSIFICATION_FAMILY_TABLE)


def _stable_residual_id(
    amending_sfs_id: str,
    base_sfs_id: str,
    section_label: str,
    classification: str,
    replay_text_head: str,
    oracle_text_head: str,
) -> str:
    """Content-addressed ``residual_id`` — stable across reruns for the same
    row-classification tuple, distinguishing every (amending, base, section,
    classification) row.

    Capped-head text is included (truncated to 64 chars, normalized) so two
    rows with the same section + classification but materially different
    replay/oracle text content produce distinct ``residual_id`` values. The
    tail text is omitted on purpose — the row's *head* carries its identity
    for triage purposes; the projection plane retains the full text value.
    """
    digest = hashlib.sha256()
    digest.update(b"se-replay-row-v0\x00")
    digest.update(f"sfs={amending_sfs_id}".encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"base={base_sfs_id}".encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"section={section_label}".encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"classification={classification}".encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"replay_head={_text_head(replay_text_head)}".encode("utf-8"))
    digest.update(b"\x00")
    digest.update(f"oracle_head={_text_head(oracle_text_head)}".encode("utf-8"))
    # ``sha256:<hex>`` form mirrors the substrate's leaf-hash string form so a
    # checker that loads residuals sees the standard content-addressed-prefix.
    return f"sha256:{digest.hexdigest()}"


def _text_head(text: str, *, cap: int = 64) -> str:
    """First ``cap`` whitespace-collapsed chars of ``text`` — sufficient
    identity to distinguish distinct row contents while keeping the hash
    input bounded and stable."""
    return " ".join(text.split())[:cap]


def se_replay_row_agreement_residual(
    row: Mapping[str, Any],
    *,
    amending_sfs_id: str,
    base_sfs_id: str,
) -> AgreementResidual:
    """Project one SE replay row into a typed ``AgreementResidual``.

    Raises ``KeyError`` if ``row.get('classification')`` is not in the closed
    family table — this is fail-loud (§1.10) by design: a new classification
    being added to ``check_se_official_replay`` MUST register a family here,
    otherwise the SE residual ledger silently drops the new class. The closed
    mapping is asserted by ``tests/test_se_agreement_residuals`` to be at
    parity with the live emit sites in ``fetch.py``.
    """
    classification = str(row.get("classification") or "")
    if classification not in _SE_CLASSIFICATION_FAMILY_TABLE:
        raise KeyError(
            f"SE replay-row classification {classification!r} has no entry in "
            f"_SE_CLASSIFICATION_FAMILY_TABLE. Either add the new row class to "
            f"`check_se_official_replay` and register its family mapping here, "
            f"or fix the row's classification string. SE residual ledger "
            f"covers exactly: {sorted(SE_REPLAY_ROW_CLASSIFICATIONS)}."
        )
    family, status, safe_default, missing_proofs = _SE_CLASSIFICATION_FAMILY_TABLE[classification]

    section_label = str(row.get("section") or "")
    replay_text = str(row.get("replay_text") or "")
    post_text = str(row.get("post_text") or "")
    residual_id = _stable_residual_id(
        amending_sfs_id=amending_sfs_id,
        base_sfs_id=base_sfs_id,
        section_label=section_label,
        classification=classification,
        replay_text_head=replay_text,
        oracle_text_head=post_text,
    )
    return AgreementResidual(
        residual_id=residual_id,
        jurisdiction=_SE_JURISDICTION,
        agreement_surface=_SE_AGREEMENT_SURFACE,
        family=cast(Any, family),
        agreement_residual_status=cast(Any, status),
        owner_phase="oracle_adjudication",
        # Uniform witness rule for the projector — the per-classification
        # semantics travel in ``detail["classification"]``. Cataloged in
        # ``_SE_RULE_SPECS`` as ``se_replay_classification_to_agreement_residual``.
        rule_id="se_replay_classification_to_agreement_residual",
        source_artifact_id=f"{amending_sfs_id}/{base_sfs_id}/{section_label}",
        replay_count=1,
        oracle_count=1,
        missing_proofs=missing_proofs,
        safe_default=safe_default,
        forbidden_shortcuts=(
            # §1.11 / §1.12: a residual does NOT authorize replay. The
            # replay-vs-oracle comparison classifies a surface; it does not
            # mutate legal state. Reclassifying a row to "agreement" must not
            # be done by side-effect to flatten an audit.
            "reclassify_to_agreement_via_projection_mutation",
            "derive_replay_authority_from_residual_family",
        ),
        detail={
            "classification": classification,
            "matched": bool(row.get("match")),
            "section": section_label,
            "amending_sfs_id": amending_sfs_id,
            "base_sfs_id": base_sfs_id,
        },
    )


def se_replay_agreement_residuals(
    replay_result: Mapping[str, Any],
) -> tuple[AgreementResidual, ...]:
    """Project the ``rows`` list of a ``check_se_official_replay`` result
    into a tuple of typed agreement residuals.

    The tuple is the evidence-plane dossier that the projection-plane dict is
    re-derived FROM — the SE residual ledger participates in the cross-
    jurisdiction substrate via the shared ``AgreementResidual`` type, and the
    ``residual_id`` is content-addressed so a checker can detect a missing or
    surplus residual between two runs.
    """
    amending_sfs_id = str(replay_result.get("amending_sfs_id") or "")
    base_sfs_id = str(replay_result.get("base_sfs_id") or "")
    rows = replay_result.get("rows") or []
    return tuple(
        se_replay_row_agreement_residual(
            row,
            amending_sfs_id=amending_sfs_id,
            base_sfs_id=base_sfs_id,
        )
        for row in rows
    )


__all__ = [
    "SE_REPLAY_ROW_CLASSIFICATIONS",
    "se_replay_agreement_residuals",
    "se_replay_row_agreement_residual",
]
