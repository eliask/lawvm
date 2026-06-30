"""``lawvm.core.projection_rederivation_audit`` — D9 ``PROJECTION.REDERIVABLE_FROM_DOSSIER``.

Per :file:`notes/LAWVM_AUDIT_REGISTRY_ROADMAP.md` §B5 (D9): every projection row
committed under a dossier — a seam/dump/viewer row, parquet/SQLite export, or
review packet — **must be re-derivable from the committed dossier**, i.e. its
committed content hash must recompute from the row's own committed
``projection_payload`` under the dossier's pinned hash-view rule (certificate
spec §3.4). A projection row whose lineage is opaque — hand-inserted, externally
edited, or served from a stale cache — has a committed hash that no longer
re-derives from its payload; this audit surfaces it as a typed
``PROJECTION.REDERIVATION_DRIFT``
:class:`~lawvm.core.phase_result.Observation`.

WHAT IT REUSES (no parallel hashing scheme; AGENTS.md §0/§2.6). The seam
dossier's projection rows are already content-hashed by
:func:`lawvm.tools.certificate_bundle.projection_payload_hash`
(``leaf_hash(D_PROJECTION_PAYLOAD, hash_view(payload, excluded))``), and the
writer-side self-check :func:`lawvm.tools.certificate_bundle.verify_bundle`
already recomputes that hash inline as one assertion among ~30 root recomputes.
This audit lifts the SAME recompute into a standalone, reusable, Finding-shaped
sweep over the committed projection rows: it imports
``projection_payload_hash`` VERBATIM rather than re-deriving the hash profile,
so the audit and the writer self-check agree byte-for-byte on what
"re-derivable" means. The novelty is the offline *audit surface* (a typed
finding stream a strict consumer can gate on), not a new hash.

PLANE & DISCIPLINE (AGENTS.md §0, §1.10, §2.10). This module lives in the
projection-plane audit lane: it inspects committed projection-row carriers
(``projection_payload`` + the committed ``projection_hash`` + the pinned
``hash_excluded_members``) and returns
:class:`~lawvm.core.phase_result.Observation` tuples. It NEVER mutates legal
state, never re-materializes the engine, never rewrites a projection row, and
never recomputes a projection from ``(base_state, ops, pit_date)`` itself — it
checks only that the COMMITTED row hashes back to its COMMITTED payload. The
re-materialization-from-engine step is owned by the dossier WRITER
(``certificate_bundle.build_certificate_bundle``); this audit is the
read-only consistency proof over the writer's committed output, exactly mirroring
the per-row half of ``verify_bundle``'s §5.5 seam check.

FAIL-LOUD (AGENTS.md §1.10). A row missing the ``projection_payload`` member,
missing the committed ``projection_hash``, or carrying a non-string hash is a
malformed dossier — a producer bug, not a corpus fact — so it raises
:class:`ProjectionRederivationInputError` rather than being silently absorbed
into a "clean" verdict. A row whose hash simply does not recompute is the
genuine drift finding and is emitted as an Observation (the §0 over-retention-safe
direction: surface the opaque row, never trust it).

WHAT THIS DOES **NOT** YET DO:
  * It checks the SEAM projection family (``lawvm.provision_state.v1``), the one
    family the experimental dossier writer emits today
    (``certificate_bundle.py`` §11.3). The dump / transition-graph / parquet /
    SQLite / review-packet families are not yet emitted by any committed
    dossier; when a writer emits them under the same ``projection_hash``
    discipline, they pass through :func:`assert_projection_rows_rederivable`
    unchanged (the audit is family-agnostic — it reads ``projection_payload`` +
    ``projection_hash`` + the pinned exclusion list, never a seam-specific
    field).
  * It does not assert the dossier's *universe totality* (that every
    ``(address, pit)`` in scope produced a row) — that parity is owned by
    ``verify_bundle``'s universe reconciliation. D9's lane is per-row
    re-derivability, not row-set completeness.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lawvm.core.phase_result import Observation
from lawvm.tools.certificate_bundle import projection_payload_hash

# Public finding code, also registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
PROJECTION_REDERIVATION_DRIFT = "PROJECTION.REDERIVATION_DRIFT"

# Audit-stage / owner used in the emitted Observations. Stage mirrors the
# projection-plane wire point (the dossier's seam-projection emission).
_PROJECTION_AUDIT_STAGE = "projection-rederivation"
_PROJECTION_AUDIT_OWNER = "projection_rederivation_audit"
_PROJECTION_AUDIT_REASON = "committed_projection_hash_does_not_recompute_from_payload"


class ProjectionRederivationInputError(ValueError):
    """A committed projection row is malformed for re-derivation (producer bug).

    Distinct from a drift *finding*: a missing payload / missing committed hash /
    non-string hash is not a corpus fact about an opaque row — it is a structural
    defect in the dossier the writer emitted, so it fails loud per AGENTS.md §1.10
    rather than being folded into a clean verdict.
    """


def _committed_hash(row: Mapping[str, Any], *, row_index: int) -> str:
    """Extract the committed ``projection_hash`` from a seam wrapper row.

    The seam dossier nests it under ``certificate.projection_hash`` (the
    parentage block ``certificate_bundle`` writes). A flat ``projection_hash``
    member is also accepted so a non-seam family carrying the hash at top level
    is auditable without a seam-specific shape. Fail-loud on absence/typing.
    """
    parentage = row.get("certificate")
    committed: Any
    if isinstance(parentage, Mapping) and "projection_hash" in parentage:
        committed = parentage["projection_hash"]
    elif "projection_hash" in row:
        committed = row["projection_hash"]
    else:
        raise ProjectionRederivationInputError(
            f"projection row #{row_index} carries no committed projection_hash "
            "(neither certificate.projection_hash nor a top-level projection_hash)"
        )
    if not isinstance(committed, str) or not committed:
        raise ProjectionRederivationInputError(
            f"projection row #{row_index} committed projection_hash is not a "
            f"non-empty string: {committed!r}"
        )
    return committed


def _committed_payload(row: Mapping[str, Any], *, row_index: int) -> Mapping[str, Any]:
    """Extract the committed ``projection_payload`` to re-hash. Fail-loud."""
    payload = row.get("projection_payload")
    if not isinstance(payload, Mapping):
        raise ProjectionRederivationInputError(
            f"projection row #{row_index} carries no projection_payload mapping "
            f"to re-derive from (found {type(payload).__name__})"
        )
    return payload


def _row_identity(row: Mapping[str, Any], row_index: int) -> dict[str, Any]:
    """Stable row id for the observation detail (universe address/interval).

    The seam wrapper carries ``universe = {"address", "interval": [from, to]}``;
    a non-seam family without it falls back to the positional row index. Never
    positional-as-identity in the dossier itself (§2.8); this is a
    triage-surface label only.
    """
    universe = row.get("universe")
    if isinstance(universe, Mapping):
        interval = universe.get("interval")
        interval_pair: tuple[Any, Any]
        if isinstance(interval, Sequence) and not isinstance(interval, (str, bytes)) and len(interval) >= 2:
            interval_pair = (interval[0], interval[1])
        else:
            interval_pair = ("", "")
        return {
            "address": universe.get("address", ""),
            "interval_from": interval_pair[0],
            "interval_to": interval_pair[1],
            "row_index": row_index,
        }
    return {"address": "", "interval_from": "", "interval_to": "", "row_index": row_index}


def _build_observation(
    row: Mapping[str, Any],
    *,
    row_index: int,
    expected_hash: str,
    actual_hash: str,
    excluded_members: tuple[str, ...],
    source_statute: str,
) -> Observation:
    """Build the typed ``PROJECTION.REDERIVATION_DRIFT`` observation.

    The detail carries the fixed-shape evidence a triager needs to answer "which
    row, expected vs actual hash, under which derivation inputs" without
    re-running the dossier writer: the row identity, the committed
    (``expected``) hash, the freshly recomputed (``actual``) hash, and the pinned
    ``hash_excluded_members`` that defines the §3.4 hash view (the derivation
    input). ``expected`` is what the dossier committed; ``actual`` is what the
    payload re-derives to — they disagree exactly when the row's lineage is
    opaque.
    """
    identity = _row_identity(row, row_index)
    parentage = row.get("certificate")
    projection_schema = ""
    projection_kind = ""
    if isinstance(parentage, Mapping):
        projection_schema = str(parentage.get("projection_schema", ""))
        projection_kind = str(parentage.get("projection_kind", ""))
    detail: dict[str, Any] = {
        "row_id": identity,
        "projection_kind": projection_kind,
        "projection_schema": projection_schema,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
        "hash_excluded_members": excluded_members,
        "reason": _PROJECTION_AUDIT_REASON,
        "owner": _PROJECTION_AUDIT_OWNER,
    }
    return Observation(
        kind=PROJECTION_REDERIVATION_DRIFT,
        stage=_PROJECTION_AUDIT_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


def assert_projection_rows_rederivable(
    projection_rows: Sequence[Mapping[str, Any]],
    *,
    hash_excluded_members: Sequence[str] = (),
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """One :class:`Observation` per projection row whose committed hash does not re-derive.

    For each committed projection row, recompute the §3.4 projection hash from the
    row's OWN committed ``projection_payload`` under ``hash_excluded_members``
    (the dossier's pinned hash-view rule), and compare to the row's committed
    ``projection_hash``. A mismatch means the committed row is not re-derivable
    from its payload — its lineage is opaque (hand-inserted, externally edited,
    or a stale cache) — and is surfaced as a typed
    ``PROJECTION.REDERIVATION_DRIFT`` observation.

    The recompute reuses
    :func:`lawvm.tools.certificate_bundle.projection_payload_hash` VERBATIM, so
    this audit and the dossier writer's ``verify_bundle`` self-check agree
    byte-for-byte on the derivation. The audit never re-materializes the engine,
    never rewrites a row, and never mutates legal state.

    Args:
        projection_rows: the committed projection rows under audit — seam wrapper
            rows (``{"projection_payload", "certificate": {"projection_hash"},
            "universe"}``) or any family carrying ``projection_payload`` plus a
            committed ``projection_hash`` (top-level or under ``certificate``).
        hash_excluded_members: the dossier's pinned §3.4 hash-view exclusion list
            (e.g. the seam family's ``hash_excluded_members`` from the projection
            spec manifest, typically ``("engine",)``). The audit reads it as a
            derivation input rather than hardcoding the table, mirroring
            ``verify_bundle``'s read of ``projections.seam.hash_excluded_members``.
        source_statute: base statute id of the dossier under audit; carried into
            each observation for multi-statute routing.

    Returns:
        Tuple of Observations, one per drifting row, in committed-row order
        (deterministic). The caller decides whether these become findings
        (quirks default) or strict-mode barriers — this function emits
        observations only, never mutates legal state. An empty input or an
        all-clean dossier returns the empty tuple.

    Raises:
        ProjectionRederivationInputError: a row is structurally malformed for
            re-derivation (no payload, no committed hash, non-string hash) — a
            producer bug, fail-loud per AGENTS.md §1.10, not a drift finding.
    """
    excluded = tuple(hash_excluded_members)
    findings: list[Observation] = []
    for row_index, row in enumerate(projection_rows):
        if not isinstance(row, Mapping):
            raise ProjectionRederivationInputError(
                f"projection row #{row_index} is not a mapping (found "
                f"{type(row).__name__})"
            )
        committed = _committed_hash(row, row_index=row_index)
        payload = _committed_payload(row, row_index=row_index)
        recomputed = projection_payload_hash(payload, excluded)
        if recomputed == committed:
            continue
        findings.append(
            _build_observation(
                row,
                row_index=row_index,
                expected_hash=committed,
                actual_hash=recomputed,
                excluded_members=excluded,
                source_statute=source_statute,
            )
        )
    return tuple(findings)


__all__ = [
    "PROJECTION_REDERIVATION_DRIFT",
    "ProjectionRederivationInputError",
    "assert_projection_rows_rederivable",
]
