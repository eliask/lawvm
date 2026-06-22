"""LawVM distributable-substrate prototype — P0 primitives.

This package is the isolated, import-light home of the substrate object-model
primitives frozen in ``notes_internal/OBJECT_MODEL_AND_PACK_V0.md``:

* :mod:`lawvm.substrate.canonical_json` — the ``lawvm.canonical_json.v1``
  identity encoding, NFC-at-construction normalization, and the
  ``{object_hash, object}`` JSONL wrapper.
* :mod:`lawvm.substrate.roots` — the four named root constructors
  (``LeafHash`` / ``SetRoot`` / ``SeqRoot`` / ``MapRoot``).
* :mod:`lawvm.substrate.hashes` — the explicit three-hash split
  (raw witness / semantic object / storage blob).
* :mod:`lawvm.substrate.manifest` — the self-describing ``PackManifest``.

The canonical-JSON profile and the ``SetRoot``/``SeqRoot``/``LeafHash``
constructors are byte-for-byte re-implementations of the verified-on-disk
trust spine (``lawvm.tools.certificate_bundle``); a test in
``tests/substrate/test_canonical_json.py`` pins equality with that source so
the two implementations cannot drift.

Phase-3 layers built on those primitives:

* :mod:`lawvm.substrate.source` — source-plane lineage (``SourceRecord`` →
  ``SourceManifestation`` → ``SourceUnit`` → ``SourceBundleVersion``,
  correction events, the delta classifier, ``InitialStateEvent`` genesis).
* :mod:`lawvm.substrate.selection` — the five-axis ``StateSelectionIndex``
  (applicability facts, selection rows, mandatory candidate sets, the closed
  ``ScopePredicate``, the selection universe, and the multi-root build).
* :mod:`lawvm.substrate.checker` — the L0/L1 checker and the two-axis
  (integrity × certification) verdict algebra.
"""

from __future__ import annotations

from lawvm.substrate.canonical_json import (
    CanonicalJsonError,
    JsonValue,
    canonical_json_bytes,
    nfc,
    semantic_hash,
    unwrap_and_verify,
    wrap_row,
)
from lawvm.substrate.checker import (
    CertificationVerdict,
    CheckerVerdict,
    CheckLevel,
    CheckMode,
    Checker,
    IntegrityVerdict,
    Pack,
    PackLayerData,
    SourceAvailability,
    TopLineVerdict,
    TypedViolation,
    ViolationCode,
    check_pack,
    fold_top_line,
)
from lawvm.substrate.hashes import (
    raw_witness_hash,
    semantic_object_hash,
    storage_blob_hash,
)
from lawvm.substrate.manifest import PackLayer, PackManifest, PackProvenance
from lawvm.substrate.relation_edge import (
    SCHEMA_RELATION_EDGE,
    AuthorityPlane,
    EdgeStatus,
    RelationKind,
    TargetSetSemantics,
    VerificationLevel,
    build_relation_edge,
    edge_authority_violation,
    recompute_edge_id,
)
from lawvm.substrate.roots import (
    RootError,
    leaf_hash,
    map_root,
    seq_root,
    set_root,
)
from lawvm.substrate.selection import (
    PROFILE_GOVERNING_TEXT,
    PROFILE_IN_FORCE_TEXT,
    PROFILE_VIEWER_DEFAULT,
    ApplicabilityFact,
    DecisionBasis,
    ScopePredicate,
    SelectionCandidate,
    SelectionCandidateSet,
    SelectionError,
    SelectionIndexRoots,
    SelectionProfile,
    SelectionRow,
    SelectionUniverse,
    StateSelectionRoots,
    TemporalBasis,
    build_selection_index_roots,
    build_state_selection_roots,
    v0_profiles,
)
from lawvm.substrate.source import (
    Availability,
    CorrigendumKind,
    ExtractionCorrectionAssertion,
    ExtractionCorrectionReason,
    GenesisKind,
    InitialStateEvent,
    KeeperCorrectionEvent,
    KeeperCorrectionReason,
    KeeperVersionHints,
    LegalEffect,
    Locator,
    LogicalKind,
    ManifestationDelta,
    OfficialCorrigendumEvent,
    PriorHistoryStatus,
    RecomputeScope,
    SourceBundleVersion,
    SourceDeltaClassification,
    SourceLocatorRef,
    SourceManifestation,
    SourcePolicy,
    SourceRecord,
    SourceRole,
    SourceUnit,
    SourceUnitDelta,
)

__all__ = [
    # canonical_json
    "JsonValue",
    "CanonicalJsonError",
    "nfc",
    "canonical_json_bytes",
    "semantic_hash",
    "wrap_row",
    "unwrap_and_verify",
    # roots
    "RootError",
    "leaf_hash",
    "seq_root",
    "set_root",
    "map_root",
    # hashes
    "raw_witness_hash",
    "semantic_object_hash",
    "storage_blob_hash",
    # manifest
    "PackManifest",
    "PackLayer",
    "PackProvenance",
    # relation_edge (universal proof-graded relation edge, design §25)
    "SCHEMA_RELATION_EDGE",
    "RelationKind",
    "TargetSetSemantics",
    "AuthorityPlane",
    "VerificationLevel",
    "EdgeStatus",
    "build_relation_edge",
    "edge_authority_violation",
    "recompute_edge_id",
    # source
    "LogicalKind",
    "Availability",
    "SourceRole",
    "LegalEffect",
    "CorrigendumKind",
    "KeeperCorrectionReason",
    "ExtractionCorrectionReason",
    "ManifestationDelta",
    "SourceUnitDelta",
    "RecomputeScope",
    "SourcePolicy",
    "GenesisKind",
    "PriorHistoryStatus",
    "SourceRecord",
    "Locator",
    "KeeperVersionHints",
    "SourceManifestation",
    "SourceLocatorRef",
    "SourceUnit",
    "SourceBundleVersion",
    "OfficialCorrigendumEvent",
    "KeeperCorrectionEvent",
    "ExtractionCorrectionAssertion",
    "SourceDeltaClassification",
    "InitialStateEvent",
    # selection
    "SelectionError",
    "PROFILE_GOVERNING_TEXT",
    "PROFILE_IN_FORCE_TEXT",
    "PROFILE_VIEWER_DEFAULT",
    "ScopePredicate",
    "TemporalBasis",
    "ApplicabilityFact",
    "SelectionCandidate",
    "SelectionCandidateSet",
    "DecisionBasis",
    "SelectionRow",
    "SelectionProfile",
    "v0_profiles",
    "SelectionUniverse",
    "StateSelectionRoots",
    "build_state_selection_roots",
    "SelectionIndexRoots",
    "build_selection_index_roots",
    # checker
    "IntegrityVerdict",
    "CertificationVerdict",
    "TopLineVerdict",
    "CheckMode",
    "CheckLevel",
    "ViolationCode",
    "TypedViolation",
    "CheckerVerdict",
    "fold_top_line",
    "PackLayerData",
    "SourceAvailability",
    "Pack",
    "Checker",
    "check_pack",
]
