"""Production loader for authored UK manual-compilation claims.

Eight opt-in claim kinds exist in ``src/lawvm/uk_legislation/`` (M1 contingent
commencement, M2 same-moment precedence, appropriate-place insert, M6 deixis-in-
application, savings-scoped omission, range-to-container, M5 application overlay,
N5 source/feed reconciliation). Each is a frozen dataclass with a uniform
``claim_from_dict`` deserializer and a deterministic ``validate_*`` gate;
``UKReplayPipeline.compile_ops_for_statute`` already accepts one opt-in
``Sequence[...]`` parameter per kind, defaulting to ``None`` ⇒ replay
byte-unchanged.

This module is the missing *production seam*: a persisted, diffable, per-statute
claim store on disk, plus a loader that reads the authored claims for a statute
and returns them bucketed by kind so the scoring/replay entry path can pass each
bucket through the matching opt-in parameter.

Store format
------------
One JSON file per statute under ``data/uk/manual_claims/<statute_id>.json`` (the
``/`` in a statute id becomes ``__`` so the path is flat and filesystem-safe). The
file is a single object::

    {
      "statute_id": "ukpga/2008/17",
      "claims": [
        {"claim_kind": "appropriate_place_definition_entry", "effect_id": "...",
         "statute_id": "ukpga/2008/17", ...},
        ...
      ]
    }

Each entry is exactly the ``to_dict()`` payload of one authored claim, carrying
its own ``claim_kind`` so the loader can route it to the right deserializer. The
format is line-diffable (sorted keys, indented) so authored claims review like
any other source.

Opt-in / empty-by-default
-------------------------
Loading is OPT-IN. The pipeline wiring only consults this store when the feature
flag is on (``uk_manual_claims_enabled()`` — env ``LAWVM_UK_MANUAL_CLAIMS`` truthy)
AND a per-statute file exists. With the flag off, or with no authored file, the
loader yields an empty bucket-set and every opt-in parameter stays ``None`` ⇒ the
replay/score path is byte-identical to today. The loader NEVER validates or
applies a claim; it only deserializes. The pipeline's per-kind ``validate_*``
gate remains the sole authority on whether an authored claim takes effect — an
invalid stored claim is rejected at the gate, never silently applied.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lawvm.uk_legislation.appropriate_place_claim import (
    AppropriatePlaceInsertClaim,
    _CLAIM_KINDS as _APPROPRIATE_PLACE_CLAIM_KINDS,
    claim_from_dict as _appropriate_place_from_dict,
)
from lawvm.uk_legislation.application_overlay_claim import (
    APPLICATION_OVERLAY_CLAIM_KIND,
    ApplicationOverlayClaim,
    claim_from_dict as _application_overlay_from_dict,
)
from lawvm.uk_legislation.contingent_commencement_claim import (
    CONDITIONAL_TEMPORAL_REPEAL_CLAIM_KIND,
    CONTINGENT_COMMENCEMENT_CLAIM_KIND,
    ContingentCommencementClaim,
    claim_from_dict as _contingent_from_dict,
)
from lawvm.uk_legislation.deixis_application_claim import (
    DEIXIS_IN_APPLICATION_CLAIM_KIND,
    DeixisInApplicationClaim,
    claim_from_dict as _deixis_from_dict,
)
from lawvm.uk_legislation.range_to_container_claim import (
    RANGE_TO_CONTAINER_CLAIM_KIND,
    RangeToContainerClaim,
    claim_from_dict as _range_from_dict,
)
from lawvm.uk_legislation.same_moment_precedence_claim import (
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND,
    SameMomentPrecedenceClaim,
    claim_from_dict as _same_moment_from_dict,
)
from lawvm.uk_legislation.savings_omission_claim import (
    SAVINGS_SCOPED_OMISSION_CLAIM_KIND,
    SavingsScopedOmissionClaim,
    claim_from_dict as _savings_from_dict,
)
from lawvm.uk_legislation.source_feed_reconciliation_claim import (
    SOURCE_FEED_RECONCILIATION_CLAIM_KIND,
    SourceFeedReconciliationClaim,
    claim_from_dict as _source_feed_from_dict,
)

# ── Feature flag ─────────────────────────────────────────────────────────────
_ENABLE_ENV_VAR = "LAWVM_UK_MANUAL_CLAIMS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def uk_manual_claims_enabled() -> bool:
    """True when authored-claim loading is opted in via the feature-flag env var.

    Empty-by-default discipline: with the flag unset/falsy the loader yields no
    claims and the replay/score path is byte-identical to today.
    """
    return str(os.environ.get(_ENABLE_ENV_VAR, "")).strip().lower() in _TRUTHY


# ── Store location ───────────────────────────────────────────────────────────
def _repo_root() -> Path:
    # src/lawvm/uk_legislation/manual_claim_store.py → parents[3] == repo root.
    return Path(__file__).resolve().parents[3]


def default_store_dir() -> Path:
    """Directory holding the per-statute authored-claim JSON files."""
    return _repo_root() / "data" / "uk" / "manual_claims"


def _statute_file_stem(statute_id: str) -> str:
    """Filesystem-safe flat stem for a statute id (``ukpga/2008/17`` → ``ukpga__2008__17``)."""
    return str(statute_id or "").replace("/", "__")


def statute_claim_path(statute_id: str, *, store_dir: Optional[Path] = None) -> Path:
    """Path to the authored-claim file for *statute_id*."""
    directory = store_dir if store_dir is not None else default_store_dir()
    return directory / f"{_statute_file_stem(statute_id)}.json"


# ── Routing: claim_kind → (bucket, deserializer) ─────────────────────────────
# A bucket name maps 1:1 onto a ``compile_ops_for_statute`` opt-in parameter.
BUCKET_CONTINGENT = "contingent_commencement_claims"
BUCKET_SAME_MOMENT = "same_moment_precedence_claims"
BUCKET_APPROPRIATE_PLACE = "appropriate_place_claims"
BUCKET_DEIXIS = "deixis_application_claims"
BUCKET_SAVINGS = "savings_omission_claims"
BUCKET_RANGE = "range_to_container_claims"
BUCKET_APPLICATION_OVERLAY = "application_overlay_claims"
BUCKET_SOURCE_FEED = "source_feed_reconciliation_claims"

ALL_BUCKETS: tuple[str, ...] = (
    BUCKET_CONTINGENT,
    BUCKET_SAME_MOMENT,
    BUCKET_APPROPRIATE_PLACE,
    BUCKET_DEIXIS,
    BUCKET_SAVINGS,
    BUCKET_RANGE,
    BUCKET_APPLICATION_OVERLAY,
    BUCKET_SOURCE_FEED,
)

# claim_kind → bucket. The appropriate-place family carries three kinds, all of
# which route to the one appropriate-place opt-in parameter.
_KIND_TO_BUCKET: dict[str, str] = {
    CONTINGENT_COMMENCEMENT_CLAIM_KIND: BUCKET_CONTINGENT,
    CONDITIONAL_TEMPORAL_REPEAL_CLAIM_KIND: BUCKET_CONTINGENT,
    SAME_MOMENT_PRECEDENCE_CLAIM_KIND: BUCKET_SAME_MOMENT,
    DEIXIS_IN_APPLICATION_CLAIM_KIND: BUCKET_DEIXIS,
    SAVINGS_SCOPED_OMISSION_CLAIM_KIND: BUCKET_SAVINGS,
    RANGE_TO_CONTAINER_CLAIM_KIND: BUCKET_RANGE,
    APPLICATION_OVERLAY_CLAIM_KIND: BUCKET_APPLICATION_OVERLAY,
    SOURCE_FEED_RECONCILIATION_CLAIM_KIND: BUCKET_SOURCE_FEED,
}
for _ap_kind in _APPROPRIATE_PLACE_CLAIM_KINDS:
    _KIND_TO_BUCKET[_ap_kind] = BUCKET_APPROPRIATE_PLACE

# bucket → deserializer.
_BUCKET_DESERIALIZER = {
    BUCKET_CONTINGENT: _contingent_from_dict,
    BUCKET_SAME_MOMENT: _same_moment_from_dict,
    BUCKET_APPROPRIATE_PLACE: _appropriate_place_from_dict,
    BUCKET_DEIXIS: _deixis_from_dict,
    BUCKET_SAVINGS: _savings_from_dict,
    BUCKET_RANGE: _range_from_dict,
    BUCKET_APPLICATION_OVERLAY: _application_overlay_from_dict,
    BUCKET_SOURCE_FEED: _source_feed_from_dict,
}


@dataclass(frozen=True)
class LoadedManualClaims:
    """Authored claims for one statute, bucketed by ``compile_ops`` opt-in param.

    Each attribute is the list to pass to the matching opt-in parameter of
    ``compile_ops_for_statute``. ``unknown_kind_rows`` records entries whose
    ``claim_kind`` did not route to any bucket (kept so a loader caller can
    surface a diagnostic rather than silently dropping authored data); these are
    NEVER passed through to the pipeline.
    """

    statute_id: str
    contingent_commencement_claims: list[ContingentCommencementClaim] = field(
        default_factory=list
    )
    same_moment_precedence_claims: list[SameMomentPrecedenceClaim] = field(
        default_factory=list
    )
    appropriate_place_claims: list[AppropriatePlaceInsertClaim] = field(
        default_factory=list
    )
    deixis_application_claims: list[DeixisInApplicationClaim] = field(
        default_factory=list
    )
    savings_omission_claims: list[SavingsScopedOmissionClaim] = field(
        default_factory=list
    )
    range_to_container_claims: list[RangeToContainerClaim] = field(default_factory=list)
    application_overlay_claims: list[ApplicationOverlayClaim] = field(
        default_factory=list
    )
    source_feed_reconciliation_claims: list[SourceFeedReconciliationClaim] = field(
        default_factory=list
    )
    unknown_kind_rows: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            getattr(self, bucket) for bucket in ALL_BUCKETS
        )

    def total_claims(self) -> int:
        return sum(len(getattr(self, bucket)) for bucket in ALL_BUCKETS)

    def compile_kwargs(self) -> dict[str, Any]:
        """Opt-in kwargs for ``compile_ops_for_statute``.

        Each bucket maps to its opt-in parameter; an empty bucket yields ``None``
        so the pipeline's no-claims byte-unchanged path is preserved exactly.
        """
        return {
            bucket: (getattr(self, bucket) or None) for bucket in ALL_BUCKETS
        }


def buckets_from_rows(statute_id: str, rows: list[dict[str, Any]]) -> LoadedManualClaims:
    """Route raw claim rows into typed buckets by ``claim_kind``.

    Pure / side-effect-free: a row with an unrecognized ``claim_kind`` is parked
    in ``unknown_kind_rows`` and never passed downstream. Deserialization does NOT
    validate — the pipeline ``validate_*`` gate is the sole authority.
    """
    loaded = LoadedManualClaims(statute_id=statute_id)
    for row in rows:
        if not isinstance(row, dict):
            loaded.unknown_kind_rows.append({"_invalid_row": repr(row)[:200]})
            continue
        kind = str(row.get("claim_kind") or "")
        bucket = _KIND_TO_BUCKET.get(kind)
        if bucket is None:
            loaded.unknown_kind_rows.append(dict(row))
            continue
        deserialize = _BUCKET_DESERIALIZER[bucket]
        getattr(loaded, bucket).append(deserialize(row))
    return loaded


def load_manual_claims_for_statute(
    statute_id: str,
    *,
    store_dir: Optional[Path] = None,
    enabled: Optional[bool] = None,
) -> LoadedManualClaims:
    """Load authored claims for *statute_id*, bucketed for the pipeline.

    Opt-in: when ``enabled`` is ``None`` the feature flag
    (``uk_manual_claims_enabled``) decides. With loading disabled, or with no
    authored file, the result is empty ⇒ the replay/score path stays
    byte-unchanged. Reads + deserializes only; never validates or applies.
    """
    if enabled is None:
        enabled = uk_manual_claims_enabled()
    if not enabled:
        return LoadedManualClaims(statute_id=statute_id)
    path = statute_claim_path(statute_id, store_dir=store_dir)
    if not path.exists():
        return LoadedManualClaims(statute_id=statute_id)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    return buckets_from_rows(statute_id, [r for r in rows if isinstance(r, dict)])


def write_manual_claims_for_statute(
    statute_id: str,
    claim_dicts: list[dict[str, Any]],
    *,
    store_dir: Optional[Path] = None,
) -> Path:
    """Persist *claim_dicts* for *statute_id* as a diffable JSON file.

    Authoring/round-trip helper. Writes sorted-key indented JSON so authored
    claims diff cleanly. Returns the written path.
    """
    path = statute_claim_path(statute_id, store_dir=store_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "statute_id": statute_id,
        "claims": list(claim_dicts),
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return path
