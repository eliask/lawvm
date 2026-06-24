"""Source-plane lineage objects (SOURCE_LINEAGE_V0.md; design §3.4).

The witness plane is **not static**: keepers re-publish, fix OCR, move URLs, and
let old bytes vanish. This module models source the way the substrate models
legal history — an append-only graph::

    SourceRecord  →  SourceManifestation  →  SourceUnit  →  SourceBundleVersion

with typed, never-overwriting correction events, an ``availability`` verdict
input, a two-level delta classifier that decides recomputation, and a **dual
``SourcePolicy``** (``built_under`` ≠ ``checkable_under``) that keeps old
certificates checkable while banning "whatever the URL returns today."

Every object here is a frozen ``@dataclass(frozen=True, slots=True)`` carrying a
``to_canonical_dict()`` (the emitted ``lawvm.canonical_json.v1`` body) and a
computed ``@property <name>_id`` derived via ``leaf_hash(domain, body_without_id)``
— the same ``_hashed_dict`` / ``pack_id`` pattern as
:class:`lawvm.substrate.manifest.PackManifest`. Semantic-text fields are
NFC-normalized at construction (§1.2). Every semantic hash is ``"sha256:"``-
prefixed for free from :func:`lawvm.substrate.roots.leaf_hash`.

This module is **jurisdiction-neutral**: no FI/UK specifics. The enums are
closed substrate vocabularies; jurisdiction strings (``"fi"``) are data, not
code paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from lawvm.substrate.canonical_json import JsonValue, nfc
from lawvm.substrate.roots import leaf_hash, set_root

# --- domain tags (the ``leaf_hash`` domain for each object family) -----------
# Correction events share one domain (``source_correction``, §4) so they root
# together under ``correction_event_root``; the delta classifier and the four
# spine objects each have their own.
_SOURCE_RECORD_DOMAIN = "source_record"
_SOURCE_MANIFESTATION_DOMAIN = "source_manifestation"
_SOURCE_UNIT_DOMAIN = "source_unit"
_SOURCE_BUNDLE_VERSION_DOMAIN = "source_bundle_version"
_SOURCE_CORRECTION_DOMAIN = "source_correction"
_SOURCE_DELTA_DOMAIN = "source_delta"
_INITIAL_STATE_EVENT_DOMAIN = "initial_state_event"


# ===========================================================================
# Closed enums (frozen substrate vocabularies)
# ===========================================================================


class LogicalKind(str, Enum):
    """What kind of logical source a :class:`SourceRecord` names (§1.1)."""

    ACT_XML = "act_xml"
    OFFICIAL_PDF = "official_pdf"
    SECTION_XML = "section_xml"
    MUNICIPAL_CODE_PDF = "municipal_code_pdf"
    GAZETTE_PDF = "gazette_pdf"


class Availability(str, Enum):
    """Can a checker still see the bytes a certificate committed to? (§2).

    A **verdict input, not a description.** ``digest_only`` / ``unknown`` / ``lost``
    drive ``UNCHECKABLE_MISSING_SOURCE`` (never ``INVALID``). For the offline
    verdict path ``available_from_keeper_at_locator`` is treated as
    ``digest_only`` (§9.4 RESOLVED) — but that is the checker's concern; this
    module stores the enum faithfully.
    """

    AVAILABLE_IN_BUNDLE = "available_in_bundle"
    AVAILABLE_IN_LAWVM_CAS = "available_in_lawvm_cas"
    AVAILABLE_IN_EXTERNAL_ARCHIVE = "available_in_external_archive"
    AVAILABLE_FROM_KEEPER_AT_LOCATOR = "available_from_keeper_at_locator"
    DIGEST_ONLY = "digest_only"
    UNKNOWN = "unknown"
    LOST = "lost"


class SourceRole(str, Enum):
    """Why a byte stream exists (§3).

    The load-bearing distinction is whether a consolidated-view change touches
    legal-state matter (``official_consolidation_checkpoint``) or only agreement
    residuals (``current_consolidation_oracle`` — COMPARISON ONLY, never replay
    authority).
    """

    ENACTED = "enacted"
    AMENDMENT = "amendment"
    COMMENCEMENT = "commencement"
    OFFICIAL_CONSOLIDATION_CHECKPOINT = "official_consolidation_checkpoint"
    CURRENT_CONSOLIDATION_ORACLE = "current_consolidation_oracle"
    PUBLIC_DISPLAY_SNAPSHOT = "public_display_snapshot"
    PREPARATORY = "preparatory"


class LegalEffect(str, Enum):
    """How a correction event retimes (or does not) a legal effect (§4).

    ``StateSelectionIndex`` consumes this directly: ``relates_back`` moves an
    ``effect_interval`` left edge to the original effective date,
    ``from_correction_date`` to the correction date, ``evidence_only`` / ``none``
    move nothing, and ``unresolved`` → the dependent selection row blocks
    (``source_policy_unclassified``).
    """

    NONE = "none"
    EVIDENCE_ONLY = "evidence_only"
    RELATES_BACK = "relates_back"
    FROM_CORRECTION_DATE = "from_correction_date"
    UNRESOLVED = "unresolved"


class CorrigendumKind(str, Enum):
    """The structural shape of an official corrigendum (§4.1)."""

    TEXT_FIX = "text_fix"
    NUMBERING_FIX = "numbering_fix"
    REFERENCE_FIX = "reference_fix"


class KeeperCorrectionReason(str, Enum):
    """Why a keeper swapped bytes without a legal correction instrument (§4.2)."""

    METADATA_FIX = "metadata_fix"
    OCR_FIX = "ocr_fix"
    RESCAN = "rescan"
    HREF_CHANGE = "href_change"
    UNKNOWN = "unknown"


class ExtractionCorrectionReason(str, Enum):
    """Why an extraction was re-asserted, raw witness unchanged (§4.3)."""

    OCR_FIX = "ocr_fix"
    EXTRACTION_FIX = "extraction_fix"
    MANUAL_RECONSTRUCTION = "manual_reconstruction"
    UNKNOWN = "unknown"


class ManifestationDelta(str, Enum):
    """The cheap, observation-only manifestation-level delta (§5)."""

    SAME_BYTES = "same_bytes"
    BYTE_CHANGED = "byte_changed"
    LOCATOR_CHANGED = "locator_changed"
    MEDIA_TYPE_CHANGED = "media_type_changed"
    FETCH_METADATA_CHANGED = "fetch_metadata_changed"
    UNAVAILABLE = "unavailable"


class SourceUnitDelta(str, Enum):
    """The delta that decides legal-state recomputation (§5).

    **Hard rule:** ``unclassified`` → ``qualified|blocked, never clean``. An
    unclassifiable delta may never silently pass as ``semantic_unit_same``.
    """

    SEMANTIC_UNIT_SAME = "semantic_unit_same"
    METADATA_ONLY_NONSEMANTIC = "metadata_only_nonsemantic"
    LOCATOR_REANCHORED = "locator_reanchored"
    SEMANTIC_METADATA_CHANGED = "semantic_metadata_changed"
    TEXT_CHANGED = "text_changed"
    STRUCTURE_CHANGED = "structure_changed"
    SOURCE_ROLE_CHANGED = "source_role_changed"
    EXTRACTION_CHANGED = "extraction_changed"
    UNCLASSIFIED = "unclassified"


class RecomputeScope(str, Enum):
    """What recomputes when a new manifestation arrives (§5 recompute mapping)."""

    NONE = "none"
    ACCOUNT_ONLY = "account_only"
    LEGAL_STATE = "legal_state"


class SourcePolicy(str, Enum):
    """The source-selection / checkability policy (§6).

    THE DUALITY: a pack records two — ``built_under`` (the selection function the
    build ran; may chase keeper updates) and ``checkable_under`` (what a third
    party verifies against; the frozen committed bundle). ``comparison_oracle_latest``
    is **never** a replay authority.
    """

    ARCHIVAL_EXACT = "archival_exact"
    KEEPER_LATEST_SEMANTIC = "keeper_latest_semantic"
    OFFICIAL_PLUS_CORRIGENDA = "official_plus_corrigenda"
    OFFICIAL_PLUS_REVIEWED_RECONSTRUCTION = "official_plus_reviewed_reconstruction"
    COMPARISON_ORACLE_LATEST = "comparison_oracle_latest"


class GenesisKind(str, Enum):
    """The typed genesis of a work's history (OBJECT_MODEL §4.5; design §4)."""

    ORIGINAL_ENACTMENT = "original_enactment"
    OFFICIAL_CONSOLIDATION_CHECKPOINT = "official_consolidation_checkpoint"
    OBSERVED_CODIFICATION_SNAPSHOT = "observed_codification_snapshot"
    OCR_RECONSTRUCTED_SNAPSHOT = "ocr_reconstructed_snapshot"
    MANUAL_RECONSTRUCTION = "manual_reconstruction"


class PriorHistoryStatus(str, Enum):
    """Honest status of history before a genesis checkpoint (OBJECT_MODEL §4.5)."""

    NONE = "none"
    UNAVAILABLE = "unavailable"
    UNMODELED = "unmodeled"
    PARTIALLY_OBSERVED = "partially_observed"


# Genesis kinds whose creation event is an observed snapshot, not a transition:
# for these ``creation_event_id`` is the immutable ``manifestation_id`` (§8.2
# RESOLVED). ``original_enactment`` instead anchors on a transition.
_SNAPSHOT_GENESIS_KINDS = frozenset(
    {
        GenesisKind.OFFICIAL_CONSOLIDATION_CHECKPOINT,
        GenesisKind.OBSERVED_CODIFICATION_SNAPSHOT,
        GenesisKind.OCR_RECONSTRUCTED_SNAPSHOT,
        GenesisKind.MANUAL_RECONSTRUCTION,
    }
)


def _enum_value(value: object) -> str:
    """The wire string for an enum-or-str field (accept either at construction)."""
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


# ===========================================================================
# §1 — the four-object spine
# ===========================================================================


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """``lawvm.source_record.v1`` — stable LOGICAL source identity (§1.1).

    The thing a URL is only a *manifestation of*. ``keeper`` IS part of identity:
    the SAME logical source mirrored by two keepers becomes two records — no
    cross-keeper dedup (§9.1 RESOLVED); supersession/agreement is expressed via
    correction/oracle edges, not by collapsing records.

    ``work_id_hint`` is a **non-authoritative** crosswalk to ``lawvm.work.v1``;
    the binding authority is the manifestation/unit's resolved ``work_id``.
    """

    jurisdiction: str
    keeper: str
    logical_kind: str
    logical_key: str
    work_id_hint: str | None = None
    schema: str = field(default="lawvm.source_record.v1")

    def _hashed_dict(self) -> dict[str, JsonValue]:
        """Identity body — keyed ONLY on jurisdiction/keeper/logical_kind/logical_key.

        No URL, no bytes, no fetch time, and **not** the non-authoritative
        ``work_id_hint`` — those never enter the logical-source identity (§1.1).
        """
        return {
            "schema": self.schema,
            "jurisdiction": self.jurisdiction,
            "keeper": self.keeper,
            "logical_kind": _enum_value(self.logical_kind),
            "logical_key": self.logical_key,
        }

    @property
    def source_record_id(self) -> str:
        return leaf_hash(_SOURCE_RECORD_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["source_record_id"] = self.source_record_id
        body["work_id_hint"] = self.work_id_hint
        return body


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a byte stream was observed (§1.2 ``locator``).

    ``scheme ∈ {farchive, url, file}``; ``byte_count`` is the observed length.
    Part of ``manifestation_id`` — a re-fetch from a moved URL is a new
    manifestation under the same record.
    """

    scheme: str
    value: str
    byte_count: int | None = None

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "scheme": self.scheme,
            "value": self.value,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class KeeperVersionHints:
    """Structured keeper version hints (§1.2 ``keeper_version_hints``).

    Observation-only metadata (``etag``/``last_modified``/keeper label/
    consolidation date). Not part of ``manifestation_id`` — two fetches agree
    iff their bytes agree, never because their etags agree.
    """

    etag: str | None = None
    last_modified: str | None = None
    keeper_version_label: str | None = None
    consolidation_date: str | None = None

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "etag": self.etag,
            "last_modified": self.last_modified,
            "keeper_version_label": self.keeper_version_label,
            "consolidation_date": self.consolidation_date,
        }


@dataclass(frozen=True, slots=True)
class SourceManifestation:
    """``lawvm.source_manifestation.v1`` — one observed byte stream (§1.2).

    Carries the design's first hash, ``raw_witness_hash`` (bytes-as-observed, NO
    normalization), the explicit ``source_record_id`` parent edge, structured
    keeper hints, and the ``availability`` verdict input. ``supersedes_*`` is a
    lineage edge inside one record (keeper re-publish), append-only — never an
    in-place byte swap.
    """

    source_record_id: str
    raw_witness_hash: str
    media_type: str
    fetched_at: str
    locator: Locator
    availability: str
    keeper_version_hints: KeeperVersionHints | None = None
    supersedes_manifestation_id: str | None = None
    schema: str = field(default="lawvm.source_manifestation.v1")

    def _hashed_dict(self) -> dict[str, JsonValue]:
        """Identity body (§1.2): {source_record_id, raw_witness_hash, media_type, fetched_at, locator}.

        Keeper hints, availability, and the supersession edge are observation /
        verdict metadata — NOT identity. Two fetches yielding the same bytes from
        the same record at the same time and locator are the same manifestation.
        """
        return {
            "schema": self.schema,
            "source_record_id": self.source_record_id,
            "raw_witness_hash": self.raw_witness_hash,
            "media_type": self.media_type,
            "fetched_at": self.fetched_at,
            "locator": self.locator.to_canonical_dict(),
        }

    @property
    def manifestation_id(self) -> str:
        return leaf_hash(_SOURCE_MANIFESTATION_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["manifestation_id"] = self.manifestation_id
        body["availability"] = _enum_value(self.availability)
        body["keeper_version_hints"] = (
            self.keeper_version_hints.to_canonical_dict()
            if self.keeper_version_hints is not None
            else None
        )
        body["supersedes_manifestation_id"] = self.supersedes_manifestation_id
        return body


@dataclass(frozen=True, slots=True)
class SourceLocatorRef:
    """The source-span coordinate carried by a :class:`SourceUnit` (§1.3).

    Reuses the substrate ``SourceLocator`` coordinate verbatim so a content
    leaf's source span and its source unit share one coordinate system.
    ``byte_span`` is ``[start, end]`` (or ``None`` for whole-artifact).
    """

    jurisdiction: str
    artifact_kind: str
    source_id: str
    quote_hash: str
    normalization_policy: str
    byte_span: tuple[int, int] | None = None

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        return {
            "jurisdiction": self.jurisdiction,
            "artifact_kind": self.artifact_kind,
            "source_id": self.source_id,
            "byte_span": list(self.byte_span) if self.byte_span is not None else None,
            "quote_hash": self.quote_hash,
            "normalization_policy": self.normalization_policy,
        }


@dataclass(frozen=True, slots=True)
class SourceUnit:
    """``lawvm.source_unit.v1`` — one extracted canonical unit (§1.3).

    Identity binds the **extraction profile + the manifestation it came from**:
    two extractions of the same bytes under different profiles are distinct units
    (extraction is matter, not witness) — intended extraction churn (§9.5).
    ``canonical_text`` is the only field that contributes the unit's semantic
    text; it is NFC-normalized at construction (§1.2).
    """

    manifestation_id: str
    source_record_id: str
    work_id: str
    source_role: str
    extraction_profile: str
    canonical_text: str
    source_locator: SourceLocatorRef
    text_profile: str = "lawvm.canon.semantic_text.v1"
    semantic_metadata: dict[str, JsonValue] | None = None
    normalization_facts: tuple[str, ...] = ()
    schema: str = field(default="lawvm.source_unit.v1")

    def __post_init__(self) -> None:
        # NFC at construction (§1.2): canonical bytes are already NFC by hash time.
        object.__setattr__(self, "canonical_text", nfc(self.canonical_text))

    def _hashed_dict(self) -> dict[str, JsonValue]:
        """Identity body (§1.3): {manifestation_id, work_id, source_role,
        extraction_profile, text_profile, source_locator}.

        Binds extraction profile + originating manifestation. ``canonical_text``
        is carried in the emitted row (its hash flows through the content-leaf /
        node-version identity), but the source unit's own id is keyed on the
        coordinate + extraction binding per the spec ``LeafHash`` inputs.
        """
        return {
            "schema": self.schema,
            "manifestation_id": self.manifestation_id,
            "work_id": self.work_id,
            "source_role": _enum_value(self.source_role),
            "extraction_profile": self.extraction_profile,
            "text_profile": self.text_profile,
            "source_locator": self.source_locator.to_canonical_dict(),
        }

    @property
    def source_unit_id(self) -> str:
        return leaf_hash(_SOURCE_UNIT_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["source_unit_id"] = self.source_unit_id
        body["source_record_id"] = self.source_record_id
        body["canonical_text"] = self.canonical_text
        body["semantic_metadata"] = (
            dict(self.semantic_metadata) if self.semantic_metadata is not None else None
        )
        body["normalization_facts"] = list(self.normalization_facts)
        return body


@dataclass(frozen=True, slots=True)
class SourceBundleVersion:
    """``lawvm.source_bundle_version.v1`` — the admitted set a build/cert commits to (§1.4).

    The **commit point**. A build under one ``SourcePolicy`` *selects*
    manifestations/units; the bundle version freezes exactly that selection so a
    certificate stays checkable under ``archival_exact`` later. ``corpus_version``
    is the discrete account handle a ``"<j>:corpus:<date>"`` token names.

    The set roots are computed from the id collections at construction via the
    substrate :func:`set_root` so the bundle commits to the membership, not just
    the listed ids.
    """

    corpus_version: str
    built_under_source_policy: str
    checkable_under_source_policy: str
    manifestation_ids: tuple[str, ...]
    source_unit_ids: tuple[str, ...]
    correction_event_ids: tuple[str, ...]
    supersedes_source_bundle_version_id: str | None = None
    schema: str = field(default="lawvm.source_bundle_version.v1")

    @property
    def manifestation_set_root(self) -> str:
        return set_root(_SOURCE_MANIFESTATION_DOMAIN, self.manifestation_ids)

    @property
    def source_unit_set_root(self) -> str:
        return set_root(_SOURCE_UNIT_DOMAIN, self.source_unit_ids)

    @property
    def correction_event_root(self) -> str:
        return set_root(_SOURCE_CORRECTION_DOMAIN, self.correction_event_ids)

    def _hashed_dict(self) -> dict[str, JsonValue]:
        """Identity body — the policy duality + the three committed set roots.

        Keying on the set roots (not the raw id lists) means the bundle id
        changes iff its committed membership changes; the lists are carried in
        the emitted row for transport.
        """
        return {
            "schema": self.schema,
            "corpus_version": self.corpus_version,
            "built_under_source_policy": _enum_value(self.built_under_source_policy),
            "checkable_under_source_policy": _enum_value(self.checkable_under_source_policy),
            "manifestation_set_root": self.manifestation_set_root,
            "source_unit_set_root": self.source_unit_set_root,
            "correction_event_root": self.correction_event_root,
            "supersedes_source_bundle_version_id": self.supersedes_source_bundle_version_id,
        }

    @property
    def source_bundle_version_id(self) -> str:
        return leaf_hash(_SOURCE_BUNDLE_VERSION_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["source_bundle_version_id"] = self.source_bundle_version_id
        body["manifestation_ids"] = list(self.manifestation_ids)
        body["source_unit_ids"] = list(self.source_unit_ids)
        body["correction_event_ids"] = list(self.correction_event_ids)
        return body


# ===========================================================================
# §4 — typed, append-only correction events (none overwrites bytes)
# ===========================================================================


@dataclass(frozen=True, slots=True)
class OfficialCorrigendumEvent:
    """``lawvm.official_corrigendum_event.v1`` — an actual legal correction instrument (§4.1).

    Reserved for a real corrigendum (oikaisu) instrument; carries a ``legal_effect``
    classification the underlying ``corrigendum.py`` structure lacks. Points at an
    ``old_source_unit_id`` / ``new_source_unit_id`` pair; overwrites no bytes.
    """

    source_record_id: str
    old_source_unit_id: str
    new_source_unit_id: str
    instrument_ref: str
    correction_kind: str
    legal_effect: str
    reason: str = "official_corrigendum"
    schema: str = field(default="lawvm.official_corrigendum_event.v1")

    def _hashed_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.schema,
            "source_record_id": self.source_record_id,
            "old_source_unit_id": self.old_source_unit_id,
            "new_source_unit_id": self.new_source_unit_id,
            "instrument_ref": self.instrument_ref,
            "correction_kind": _enum_value(self.correction_kind),
            "reason": self.reason,
            "legal_effect": _enum_value(self.legal_effect),
        }

    @property
    def correction_event_id(self) -> str:
        return leaf_hash(_SOURCE_CORRECTION_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["correction_event_id"] = self.correction_event_id
        return body


@dataclass(frozen=True, slots=True)
class KeeperCorrectionEvent:
    """``lawvm.keeper_correction_event.v1`` — a keeper swapping bytes (§4.2).

    A keeper-side XML/OCR/metadata/href fix **without** a legal correction
    instrument. Points at both the manifestation pair and the resulting unit
    pair; overwrites no bytes (the old manifestation stays).
    """

    source_record_id: str
    old_manifestation_id: str
    new_manifestation_id: str
    old_source_unit_id: str
    new_source_unit_id: str
    reason: str
    legal_effect: str
    schema: str = field(default="lawvm.keeper_correction_event.v1")

    def _hashed_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.schema,
            "source_record_id": self.source_record_id,
            "old_manifestation_id": self.old_manifestation_id,
            "new_manifestation_id": self.new_manifestation_id,
            "old_source_unit_id": self.old_source_unit_id,
            "new_source_unit_id": self.new_source_unit_id,
            "reason": _enum_value(self.reason),
            "legal_effect": _enum_value(self.legal_effect),
        }

    @property
    def correction_event_id(self) -> str:
        return leaf_hash(_SOURCE_CORRECTION_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["correction_event_id"] = self.correction_event_id
        return body


@dataclass(frozen=True, slots=True)
class ExtractionCorrectionAssertion:
    """``lawvm.extraction_correction_assertion.v1`` — a re-extraction the build authored (§4.3).

    Raw witness **unchanged** (same manifestation); a new accepted reconstruction
    of the unit. The assertion is matter the build authored, not a keeper claim —
    so it is the lineage edge linking the old→new unit across extraction churn
    (§9.5), making profile improvement auditable rather than a silent identity break.
    """

    manifestation_id: str
    old_source_unit_id: str
    new_source_unit_id: str
    reason: str
    legal_effect: str
    asserted_under_review: bool = True
    schema: str = field(default="lawvm.extraction_correction_assertion.v1")

    def _hashed_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.schema,
            "manifestation_id": self.manifestation_id,
            "old_source_unit_id": self.old_source_unit_id,
            "new_source_unit_id": self.new_source_unit_id,
            "reason": _enum_value(self.reason),
            "asserted_under_review": self.asserted_under_review,
            "legal_effect": _enum_value(self.legal_effect),
        }

    @property
    def correction_event_id(self) -> str:
        return leaf_hash(_SOURCE_CORRECTION_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["correction_event_id"] = self.correction_event_id
        return body


# ===========================================================================
# §5 — the two-level source delta classifier
# ===========================================================================


@dataclass(frozen=True, slots=True)
class SourceDeltaClassification:
    """``lawvm.source_delta_classification.v1`` — decides what recomputes (§5).

    Two levels: the manifestation delta is cheap and observation-only; the
    **SourceUnit delta is the one that decides legal-state recomputation**.

    **Hard rule (enforced at construction):** ``unclassified`` →
    ``recompute_scope ∈ {account_only(qualified)|legal_state(blocked)}``, never
    ``none`` / ``clean``. An unclassifiable delta may never silently pass as
    ``semantic_unit_same``.
    """

    source_record_id: str
    from_manifestation_id: str
    to_manifestation_id: str
    manifestation_delta: str
    from_source_unit_id: str
    to_source_unit_id: str
    source_unit_delta: str
    recompute_scope: str
    classifier_profile: str = "lawvm.sourcedelta.v0"
    schema: str = field(default="lawvm.source_delta_classification.v1")

    def __post_init__(self) -> None:
        unit_delta = _enum_value(self.source_unit_delta)
        scope = _enum_value(self.recompute_scope)
        if unit_delta == SourceUnitDelta.UNCLASSIFIED.value and scope == RecomputeScope.NONE.value:
            raise ValueError(
                "source_unit_delta=unclassified may never map to recompute_scope=none "
                "(§5 hard rule: unclassified → qualified|blocked, never clean)"
            )

    def _hashed_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.schema,
            "source_record_id": self.source_record_id,
            "from_manifestation_id": self.from_manifestation_id,
            "to_manifestation_id": self.to_manifestation_id,
            "manifestation_delta": _enum_value(self.manifestation_delta),
            "from_source_unit_id": self.from_source_unit_id,
            "to_source_unit_id": self.to_source_unit_id,
            "source_unit_delta": _enum_value(self.source_unit_delta),
            "recompute_scope": _enum_value(self.recompute_scope),
            "classifier_profile": self.classifier_profile,
        }

    @property
    def delta_id(self) -> str:
        return leaf_hash(_SOURCE_DELTA_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["delta_id"] = self.delta_id
        return body


# ===========================================================================
# §4.5 (OBJECT_MODEL) — typed genesis
# ===========================================================================


@dataclass(frozen=True, slots=True)
class InitialStateEvent:
    """``lawvm.initial_state_event.v1`` — typed genesis (OBJECT_MODEL §4.5; design §4).

    Makes a LOCUS snapshot, a PDF ordinance, and an originally-enacted act the
    same kind of object. ``prior_history_status`` solves the non-digitized-base
    problem: admit a checkpoint, replay forward, mark prior history honestly.

    For snapshot genesis kinds (``official_consolidation_checkpoint`` /
    ``observed_codification_snapshot`` / ``ocr_reconstructed_snapshot`` /
    ``manual_reconstruction``) the ``creation_event_id`` is the immutable
    :attr:`SourceManifestation.manifestation_id` (§8.2 RESOLVED) — a stable
    anchor that does not churn on re-fetch. For ``original_enactment`` the
    creation event is instead a transition, so ``creation_event_id`` is left
    ``None`` here (the address node carries the transition anchor).
    """

    work_id: str
    genesis_kind: str
    effective_date: str
    prior_history_status: str
    source_refs: tuple[str, ...]
    creation_event_id: str | None = None
    schema: str = field(default="lawvm.initial_state_event.v1")

    def __post_init__(self) -> None:
        kind = _enum_value(self.genesis_kind)
        is_snapshot = kind in {k.value for k in _SNAPSHOT_GENESIS_KINDS}
        if is_snapshot and self.creation_event_id is None:
            raise ValueError(
                f"genesis_kind={kind} is a snapshot genesis: creation_event_id must be the "
                "immutable SourceManifestation.manifestation_id (§8.2 RESOLVED), not None"
            )

    def _hashed_dict(self) -> dict[str, JsonValue]:
        return {
            "schema": self.schema,
            "work_id": self.work_id,
            "genesis_kind": _enum_value(self.genesis_kind),
            "effective_date": self.effective_date,
            "prior_history_status": _enum_value(self.prior_history_status),
            "source_refs": list(self.source_refs),
            "creation_event_id": self.creation_event_id,
        }

    @property
    def initial_state_event_id(self) -> str:
        return leaf_hash(_INITIAL_STATE_EVENT_DOMAIN, self._hashed_dict())

    def to_canonical_dict(self) -> dict[str, JsonValue]:
        body = self._hashed_dict()
        body["initial_state_event_id"] = self.initial_state_event_id
        return body
