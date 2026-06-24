"""KNOW source-monotonicity invariants over a source-record read-model.

The external source plane is **not static**: keepers re-publish, fix OCR, move
URLs, issue corrigenda, and let old bytes vanish.  The KNOW family states the
discipline that plane must obey so a proof-carrying compiler never silently
trusts "whatever the locator returns today":

  * **KNOW-01 (source-monotonicity / append-only).**  Every external source
    update creates a NEW manifestation under a new locator/digest; it never
    mutates prior matter.  Concretely: a single stable *locator* must never be
    observed carrying two DISTINCT content digests.  Two observations of the
    same locator with the same digest are monotonic (silent).  The same digest
    under two distinct locators is a mirror/republish (NOT a violation).  A
    second digest behind the SAME locator is an in-place byte swap — the one
    thing the append-only plane forbids.

  * **KNOW-03 (lost source -> UNCHECKABLE, never INVALID).**  A source record
    whose bytes/digest are not resolvable (referenced-only, lost, digest
    unknown) is UNCHECKABLE for monotonicity.  The honest verdict for absent
    bytes is "cannot check", never "invalid".  Such records are partitioned out
    of the monotonicity check and reported separately — they neither pass nor
    fail KNOW-01.

This module is jurisdiction-NEUTRAL.  It operates on a small read-model
(:class:`SourceObservation`) that any jurisdiction's source-record stream can
be projected into.  In Finland the populated witness is the consolidated
corrigendum source corpus (``data/finland/corrigendum_sources_fi.jsonl`` via
``lawvm.finland.corrigendum_records.load_source_records``): 998 distinct
corrigendum-PDF locators, EVERY one carrying a real ``sha256`` digest — a fully
populated, byte-monotonic KNOW-01 subject (no locator carries two distinct
digests).  NB: the ``date_status == "xml_ref_without_date"`` subset (362
records) lacks a *publication date*, NOT bytes — those PDFs were still fetched
and hashed, so they are AVAILABLE, not lost.  This corpus therefore offers a
real KNOW-01 witness but **no** real KNOW-03 (lost-source) witness; KNOW-03 is
exercised only at the unit level until a digest-less source record appears.

HONESTY BOUNDARY — what this module does NOT yet compute
--------------------------------------------------------
* It does **not** check **KNOW-02** ("every 'latest' answer NAMES the source
  policy that selected that manifestation").  The FI corrigendum source records
  carry no ``SourcePolicy`` field and the substrate
  :class:`lawvm.substrate.source.SourceBundleVersion` policy duality is not yet
  wired into the FI pipeline, so there is no populated subject for KNOW-02.
* It does **not** check **KNOW-04** ("retractions taint downstream by graph
  query, not stored mutable taint").  No retraction graph over these source
  records is populated, so KNOW-04 has no claim surface here.
* "Digest resolvable" means a content digest is *present on the record*.  This
  module does NOT re-fetch bytes or verify that a digest still resolves to live
  bytes; an ``AVAILABLE``-classified observation asserts a recorded digest, not
  a successful re-read.  KNOW-01 here is a record-internal consistency check
  over recorded digests, not a liveness probe.

It is a passive auditor: it reads a source-record model and returns findings;
it authorizes no replay and asserts no legal meaning.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias, Union

from lawvm.core.frozen_values import freeze_mapping

# A canonical-JSON value — a NAMED carrier so ``to_dict`` boundaries are typed
# (not an untyped ``dict[str, object]`` phase-boundary carrier; FW-09).
JsonValue: TypeAlias = Union[
    str, int, float, bool, None, list["JsonValue"], dict[str, "JsonValue"]
]
JsonDict: TypeAlias = dict[str, JsonValue]

# Finding codes — registered in lawvm.core.observation_registry.
SOURCE_LOCATOR_DIGEST_CONFLICT = "EVID.SOURCE_LOCATOR_DIGEST_CONFLICT"
SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST = (
    "EVID.SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST"
)


class CheckabilityStatus(str, Enum):
    """Can a source observation's bytes be checked for monotonicity? (KNOW-03).

    A verdict input, not a description.  ``AVAILABLE`` carries a recorded
    content digest and participates in the KNOW-01 monotonicity check.
    ``UNCHECKABLE`` carries no resolvable digest and is partitioned OUT of the
    check — it can never become a KNOW-01 violation (absent bytes => cannot
    check, never invalid).
    """

    AVAILABLE = "available"
    UNCHECKABLE = "uncheckable"


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One observation of an external source artifact (the KNOW read-model).

    The minimal projection KNOW needs: a stable ``locator`` (the thing the
    append-only rule is keyed on), an optional ``digest`` (the recorded content
    identity), and free-form ``metadata`` for self-evidencing detail.  A source
    record carrying no usable digest is classified :attr:`CheckabilityStatus`
    ``UNCHECKABLE``; one carrying a digest is ``AVAILABLE``.
    """

    locator: str
    digest: str | None = None
    digest_algorithm: str = "sha256"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "locator", str(self.locator or ""))
        digest = self.digest
        object.__setattr__(self, "digest", str(digest) if digest else None)
        object.__setattr__(
            self, "digest_algorithm", str(self.digest_algorithm or "sha256")
        )
        if not self.locator:
            raise ValueError("SourceObservation.locator is required (the monotonicity key)")
        object.__setattr__(self, "metadata", freeze_mapping(dict(self.metadata)))

    @property
    def checkability(self) -> CheckabilityStatus:
        return (
            CheckabilityStatus.AVAILABLE
            if self.digest
            else CheckabilityStatus.UNCHECKABLE
        )

    @property
    def digest_key(self) -> str:
        """Algorithm-qualified digest identity (so two algorithms never collide)."""
        return f"{self.digest_algorithm}:{self.digest}" if self.digest else ""


@dataclass(frozen=True, slots=True)
class KnowFinding:
    """A single KNOW invariant finding (self-evidencing — carries the witness)."""

    code: str
    locator: str
    detail: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", freeze_mapping(dict(self.detail)))

    def to_dict(self) -> JsonDict:
        return {"code": self.code, "locator": self.locator, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class SourceMonotonicityReport:
    """The KNOW-01 + KNOW-03 verdict over a stream of source observations.

    Partition discipline (totality): every observation lands in exactly one of
    the three counts — ``available`` observations either witness a
    locator-digest conflict (KNOW-01 violation) or are monotonic, and
    ``uncheckable`` observations are reported separately (KNOW-03), never
    silently dropped and never counted as violations.
    """

    observation_count: int
    available_count: int
    uncheckable_count: int
    distinct_locator_count: int
    conflict_findings: tuple[KnowFinding, ...]
    uncheckable_findings: tuple[KnowFinding, ...]

    @property
    def monotonic(self) -> bool:
        """True iff no available locator carries two distinct digests (KNOW-01)."""
        return not self.conflict_findings

    @property
    def findings(self) -> tuple[KnowFinding, ...]:
        return (*self.conflict_findings, *self.uncheckable_findings)

    def to_dict(self) -> JsonDict:
        return {
            "schema": "lawvm.know_source_monotonicity_report.v1",
            "truth_claim": (
                "KNOW-01 source-monotonicity (no in-place byte swap behind a "
                "locator) over digest-bearing observations; KNOW-03 absent-bytes "
                "observations reported UNCHECKABLE, never INVALID"
            ),
            "observation_count": self.observation_count,
            "available_count": self.available_count,
            "uncheckable_count": self.uncheckable_count,
            "distinct_locator_count": self.distinct_locator_count,
            "monotonic": self.monotonic,
            "conflict_findings": [f.to_dict() for f in self.conflict_findings],
            "uncheckable_findings": [f.to_dict() for f in self.uncheckable_findings],
            "not_computed": ["KNOW-02:no_source_policy_subject", "KNOW-04:no_retraction_graph"],
        }


def check_source_monotonicity(
    observations: Iterable[SourceObservation],
) -> SourceMonotonicityReport:
    """Check KNOW-01 (append-only) + KNOW-03 (uncheckable) over observations.

    KNOW-01: group ``AVAILABLE`` observations by ``locator``; a locator mapped
    to two or more DISTINCT digests is an in-place byte mutation — one
    self-evidencing :data:`SOURCE_LOCATOR_DIGEST_CONFLICT` finding per offending
    locator, carrying the conflicting digests verbatim.

    KNOW-03: every ``UNCHECKABLE`` observation yields one
    :data:`SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST` finding and is partitioned
    OUT of the monotonicity check — it can never be a KNOW-01 violation.
    """
    obs = list(observations)

    digests_by_locator: dict[str, set[str]] = defaultdict(set)
    metadata_by_locator: dict[str, dict[str, object]] = defaultdict(dict)
    available_count = 0
    uncheckable_findings: list[KnowFinding] = []

    for ob in obs:
        if ob.checkability is CheckabilityStatus.UNCHECKABLE:
            uncheckable_findings.append(
                KnowFinding(
                    code=SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST,
                    locator=ob.locator,
                    detail={
                        "checkability": CheckabilityStatus.UNCHECKABLE.value,
                        "reason": "no_resolvable_content_digest",
                        **dict(ob.metadata),
                    },
                )
            )
            continue
        available_count += 1
        digests_by_locator[ob.locator].add(ob.digest_key)
        # Keep first-seen metadata for the locator (self-evidencing detail).
        if ob.locator not in metadata_by_locator:
            metadata_by_locator[ob.locator] = dict(ob.metadata)

    conflict_findings: list[KnowFinding] = []
    for locator, digests in digests_by_locator.items():
        if len(digests) > 1:
            conflict_findings.append(
                KnowFinding(
                    code=SOURCE_LOCATOR_DIGEST_CONFLICT,
                    locator=locator,
                    detail={
                        "conflicting_digests": sorted(digests),
                        "distinct_digest_count": len(digests),
                        **metadata_by_locator.get(locator, {}),
                    },
                )
            )

    return SourceMonotonicityReport(
        observation_count=len(obs),
        available_count=available_count,
        uncheckable_count=len(uncheckable_findings),
        distinct_locator_count=len(digests_by_locator),
        conflict_findings=tuple(
            sorted(conflict_findings, key=lambda f: f.locator)
        ),
        uncheckable_findings=tuple(
            sorted(uncheckable_findings, key=lambda f: f.locator)
        ),
    )


def source_observations_from_records(
    records: Sequence[Mapping[str, object]],
    *,
    locator_field: str = "source_pdf",
    digest_field: str = "sha256",
    digest_algorithm: str = "sha256",
    metadata_fields: Sequence[str] = (
        "statute_id",
        "amendment_id",
        "date_published",
        "date_status",
    ),
) -> tuple[SourceObservation, ...]:
    """Project source-record mappings into KNOW :class:`SourceObservation`s.

    Defaults match the FI corrigendum source record shape
    (``corrigendum_sources_fi.jsonl``): ``source_pdf`` locator + ``sha256``
    digest, with ``date_status == "xml_ref_without_date"`` records yielding no
    digest (=> UNCHECKABLE under KNOW-03).  An empty/absent digest field always
    means UNCHECKABLE — the record is never silently dropped, never forced to a
    digest it does not have.
    """
    out: list[SourceObservation] = []
    for record in records:
        locator = str(record.get(locator_field) or "")
        if not locator:
            continue
        digest = str(record.get(digest_field) or "") or None
        metadata = {k: record.get(k) for k in metadata_fields if record.get(k) is not None}
        out.append(
            SourceObservation(
                locator=locator,
                digest=digest,
                digest_algorithm=digest_algorithm,
                metadata=metadata,
            )
        )
    return tuple(out)


__all__ = [
    "CheckabilityStatus",
    "KnowFinding",
    "SourceMonotonicityReport",
    "SourceObservation",
    "SOURCE_LOCATOR_DIGEST_CONFLICT",
    "SOURCE_WITNESS_UNCHECKABLE_MISSING_DIGEST",
    "check_source_monotonicity",
    "source_observations_from_records",
]
